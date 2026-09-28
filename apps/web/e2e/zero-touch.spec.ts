import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { chromium, expect, test, type Page } from "@playwright/test";

/**
 * Zero-touch bench workflow (ADR 0018), fake models, hands-free profile.
 *
 * Protocol import and approval stay screen-based on purpose (they happen before
 * gloving up), so they are done here through the API rather than the page.
 * From the moment the session page opens, the only non-voice interaction is
 * the single "Tap once to start hands-free" gesture that browsers require.
 * Every other step is an injected wake-phrase speech segment ("FAKE:" audio
 * that the deterministic fake transcriber reads back) delivered through the
 * development-only listener seam; holding up a different bottle is simulated
 * through the development-only held-label seam. The page counts every trusted
 * pointer, mouse, touch and keyboard event, and the test fails on more than one.
 *
 * Evidence class: fake/fixture.
 */

const SAMPLE = resolve(
  process.cwd(),
  "..",
  "..",
  "sample_data",
  "protocols",
  "development-only-ethanol-protocol.docx",
);

async function installHandsFreeHarness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type Options = {
      onSegment: (audio: Blob) => void;
      onSpeechStart?: () => void;
    };
    const w = window as unknown as {
      __spoken: string[];
      __touches: string[];
      __vad: Options | null;
      __labjarvisTestListener: (options: Options) => Promise<unknown>;
      __say: (text: string) => void;
      SpeechSynthesisUtterance: unknown;
    };
    w.__spoken = [];
    w.__touches = [];
    w.__vad = null;
    for (const type of ["pointerdown", "mousedown", "touchstart", "keydown"]) {
      window.addEventListener(
        type,
        (event) => {
          if (event.isTrusted) w.__touches.push(type);
        },
        { capture: true },
      );
    }
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: () => Promise.resolve(new MediaStream()) },
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        speaking: false,
        cancel: () => undefined,
        speak: (utterance: { text: string }) => w.__spoken.push(utterance.text),
      },
    });
    w.SpeechSynthesisUtterance = class {
      public constructor(public text: string) {}
    };
    // The scripted microphone: the listener factory the app loads in place of
    // the ONNX voice-activity detector in development builds.
    w.__labjarvisTestListener = (options: Options) => {
      w.__vad = options;
      return Promise.resolve({
        start: () => Promise.resolve(),
        pause: () => Promise.resolve(),
        destroy: () => Promise.resolve(),
      });
    };
    w.__say = (text: string) => {
      const vad = w.__vad;
      if (vad === null) throw new Error("the listener is not running");
      vad.onSpeechStart?.();
      vad.onSegment(new Blob([`FAKE:${text}`], { type: "audio/wav" }));
    };
  });
}

async function openApprovedSession(page: Page): Promise<string> {
  const upload = await page.request.post("/api/v1/protocols/upload", {
    multipart: {
      file: {
        name: "development-only-ethanol-protocol.docx",
        mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buffer: readFileSync(SAMPLE),
      },
    },
  });
  expect(upload.status()).toBe(201);
  const imported = (await upload.json()) as { version: { id: string; revision: number } };
  const approved = await page.request.post(
    `/api/v1/protocol-versions/${imported.version.id}/approve-and-start`,
    { headers: { "If-Match": `"${imported.version.revision}"` } },
  );
  expect(approved.status()).toBe(201);
  const created = (await approved.json()) as { session: { id: string } };
  return created.session.id;
}

async function spoken(page: Page): Promise<string> {
  return page.evaluate(() => (window as unknown as { __spoken: string[] }).__spoken.join(" | "));
}

async function spokenCount(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __spoken: string[] }).__spoken.length);
}

let utterances = 0;

/** Say one wake-phrase utterance and wait until LabJarvis has spoken about it. */
async function say(page: Page, text: string, expected: RegExp): Promise<void> {
  utterances += 1;
  const before = await spokenCount(page);
  await page.waitForFunction(
    () => (window as unknown as { __vad: unknown }).__vad !== null,
    undefined,
    { timeout: 10_000 },
  );
  await page.evaluate((utterance) => {
    (window as unknown as { __say: (value: string) => void }).__say(utterance);
  }, text);
  await expect
    .poll(
      async () =>
        page.evaluate(
          (from) => (window as unknown as { __spoken: string[] }).__spoken.slice(from).join(" | "),
          before,
        ),
      { timeout: 15_000, message: `after saying "${text}"` },
    )
    .toMatch(expected);
  // Let the app settle (refresh, re-render) before the next utterance.
  await page.waitForFunction(() => !document.querySelector("[aria-busy='true']"));
}

async function touches(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __touches: string[] }).__touches);
}

test("a full protocol is run by voice alone after one tap", async ({ page }) => {
  test.setTimeout(180_000);
  await installHandsFreeHarness(page);
  await page.goto("/");
  const sessionId = await openApprovedSession(page);
  await page.goto(`/#/sessions/${sessionId}`);

  // ---- the one allowed touch --------------------------------------------
  const start = page.getByRole("button", { name: "Tap once to start hands-free" });
  await expect(start).toBeVisible();
  await start.click();
  await expect.poll(() => spoken(page)).toMatch(/Hands-free is on/);
  expect((await touches(page)).length).toBeGreaterThanOrEqual(1);
  const afterTap = (await touches(page)).length;

  // ---- open the session --------------------------------------------------
  await say(page, "Jarvis, start the session", /confirm start session/);
  await say(page, "Jarvis, confirm start session", /Session started/);
  await expect(
    page.getByRole("heading", { name: "Add 10 mL ethanol to the demonstration vessel." }),
  ).toBeVisible();

  // ---- ask the protocol, then record and confirm a measurement -----------
  await say(
    page,
    "Jarvis, how long should I incubate the synthetic sample?",
    /Step 2 says: Incubate the synthetic sample for 5 minutes\./,
  );
  await say(page, "Jarvis, what is the centrifuge speed?", /couldn't find that in the approved protocol/);
  await say(page, "Jarvis, record the volume as 10 mL", /confirm 10 millilitres/);
  await say(page, "Jarvis, yes", /could not act on that/); // a bare yes never commits
  await say(page, "Jarvis, confirm 10 millilitres", /Recorded 10 millilitres for volume in step 1/);
  await say(page, "Jarvis, what did I just record", /The last entry was volume 10 millilitres/);

  // ---- listener controls ---------------------------------------------------
  await say(page, "Jarvis, stop listening", /Listening paused/);
  await expect(page.getByText(/only "Jarvis, start listening" is recognised/)).toBeVisible();
  await say(page, "Jarvis, start listening", /Listening again/);
  await say(page, "Jarvis, say that again", /Listening again/);

  // ---- reagent by name with live scan: a match, then a mismatch ---------
  await say(page, "Jarvis, check the ethanol", /Label check: match for ethanol/);
  await say(page, "Jarvis, confirm ethanol", /Confirmed: the visible label matches ethanol/);
  await page.evaluate(() => {
    (window as unknown as { __labjarvisHeldLabel: string }).__labjarvisHeldLabel = "acetone";
  });
  await say(page, "Jarvis, check the ethanol", /Label check: (mismatch|uncertain)/i);
  await say(page, "Jarvis, confirm ethanol", /can never be confirmed/);
  await say(page, "Jarvis, reject", /Rejected/);
  await page.evaluate(() => {
    (window as unknown as { __labjarvisHeldLabel: string }).__labjarvisHeldLabel = "";
  });

  // ---- a timer, its alarm, and acknowledging it -------------------------
  await say(page, "Jarvis, set a timer for 2 seconds", /confirm 2 seconds/);
  await say(page, "Jarvis, confirm 2 seconds", /Started a 2 seconds timer/);
  await expect.poll(() => spoken(page), { timeout: 15_000 }).toMatch(/Timer step 1 is done/);
  await expect(page.getByText(/ALARM: Timer step 1 is done/)).toBeVisible();
  await say(page, "Jarvis, stop the alarm", /Alarm stopped/);
  await expect(page.getByText(/ALARM: Timer/)).toHaveCount(0);
  // The echo guard drops speech that starts within 1 s of LabJarvis' own sound
  // (the alarm tone included), so the operator waits a moment, as in the lab.
  await page.waitForTimeout(2_000);

  // ---- pausing and cancelling a timer --------------------------------------
  await say(page, "Jarvis, set a timer for 10 minutes", /confirm 10 minutes/);
  await say(page, "Jarvis, confirm 10 minutes", /Started a 10 minutes timer/);
  await say(page, "Jarvis, how long is left", /Active timers: .*left/);
  await say(page, "Jarvis, pause the timer", /confirm pause timer/);
  await say(page, "Jarvis, confirm pause timer", /Paused the 10 minute timer/);
  await say(page, "Jarvis, cancel the 10 minute timer", /confirm cancel 10 minute timer/);
  await say(page, "Jarvis, confirm cancel 10 minute timer", /Cancelled the 10 minute timer/);

  // ---- complete step 1, skip step 2 with a reason ------------------------
  await say(page, "Jarvis, complete the step", /confirm step 1/);
  await say(page, "Jarvis, confirm step 1", /Step 1 complete/);
  await say(page, "Jarvis, skip this step", /Say why/);
  await say(
    page,
    "Jarvis, skip this step because the incubator is unavailable",
    /confirm skip step 2/,
  );
  await say(page, "Jarvis, confirm skip step 2", /Skipped step 2/);

  // ---- correct the last measurement, then end the session ----------------
  await say(
    page,
    "Jarvis, correct the last measurement to 12 millilitres",
    /from 10 millilitres to 12 millilitres/,
  );
  await say(page, "Jarvis, confirm 12 millilitres", /Corrected volume to 12 millilitres/);
  await say(page, "Jarvis, end the session", /confirm end session/);
  await say(page, "Jarvis, confirm end session", /Ended the session early|ended/i);
  await expect(page).toHaveURL(new RegExp(`#/sessions/${sessionId}/notebook`));

  // ---- the touch budget -------------------------------------------------------
  const all = await touches(page);
  expect(all.length - afterTap, `non-voice interactions after the tap: ${all.join(",")}`).toBe(0);
  // The tap itself is pointerdown + mousedown of one gesture.
  expect(all.filter((type) => type === "pointerdown").length).toBe(1);
  expect(all.filter((type) => type === "keydown").length).toBe(0);
  expect((await spoken(page)).toLowerCase()).not.toContain("jarvis");
  // Recorded in the evidence pack (fake/fixture class); copied from here.
  const outputDir = resolve(process.cwd(), "..", "..", "output");
  mkdirSync(outputDir, { recursive: true });
  writeFileSync(
    resolve(outputDir, "zero-touch-result.json"),
    JSON.stringify(
      {
        test: "a full protocol is run by voice alone after one tap",
        finished_at: new Date().toISOString(),
        evidence_class: "fake/fixture (deterministic FAKE: speech, hands-free profile)",
        gestures: 1,
        trusted_events: all,
        non_voice_interactions_after_tap: all.length - afterTap,
        wake_phrase_utterances: utterances,
        spoken_outputs: await spokenCount(page),
        wake_phrase_in_spoken_output: (await spoken(page)).toLowerCase().includes("jarvis"),
      },
      null,
      1,
    ),
  );
  console.log(
    `ZERO_TOUCH_RESULT gestures=1 trusted_events=${all.join("+")} ` +
      `non_voice_after_tap=${all.length - afterTap} utterances=${utterances}`,
  );
});

test("kiosk launch: with the autoplay flag on localhost, no touch at all", async ({ baseURL }) => {
  test.setTimeout(90_000);
  // The flag `python scripts/tasks.py hands-free` passes to Chrome.
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  try {
    const page = await browser.newPage(baseURL === undefined ? {} : { baseURL });
    await installHandsFreeHarness(page);
    await page.goto("/");
    const sessionId = await openApprovedSession(page);
    await page.goto(`/?kiosk=1#/sessions/${sessionId}`);

    await expect(page.getByText(/Kiosk mode \(this computer only\)/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Tap once to start hands-free" })).toHaveCount(0);
    await say(page, "Jarvis, start the session", /confirm start session/);
    await say(page, "Jarvis, confirm start session", /Session started/);
    await say(page, "Jarvis, record the volume as 10 mL", /confirm 10 millilitres/);
    await say(page, "Jarvis, confirm 10 millilitres", /Recorded 10 millilitres for volume in step 1/);

    const all = await touches(page);
    expect(all, `non-voice interactions: ${all.join(",")}`).toHaveLength(0);
    console.log(`KIOSK_RESULT trusted_events=${all.length}`);
  } finally {
    await browser.close();
  }
});
