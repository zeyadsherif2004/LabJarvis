import { expect, test, type Page } from "@playwright/test";

import { startReviewedSession } from "./helpers";

/**
 * The sterile end-state journey, driven only by a push-to-talk pedal.
 *
 * After the session is started, the operator never clicks or types: every
 * utterance is a press-and-release of the pedal key (a USB foot pedal emulates
 * a keyboard key), and the label check is started by voice. The fake profile
 * transcribes deterministic "FAKE:" audio, so the in-page microphone and
 * speech synthesiser are replaced with stand-ins that replay a script of
 * utterances and record what LabJarvis says. No application code is patched.
 */

const PEDAL = "F8";

async function installMicrophoneAndSpeaker(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as {
      __utterances: string[];
      __spoken: string[];
      MediaRecorder: unknown;
    };
    w.__utterances = [];
    w.__spoken = [];
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: () => Promise.resolve(new MediaStream()) },
    });
    class ScriptedRecorder extends EventTarget {
      public mimeType = "audio/webm";
      public static isTypeSupported(): boolean {
        return true;
      }
      public start(): void {}
      public stop(): void {
        const text = w.__utterances.shift() ?? "";
        const event = new Event("dataavailable") as Event & { data: Blob };
        event.data = new Blob([`FAKE:${text}`], { type: "audio/webm" });
        this.dispatchEvent(event);
        this.dispatchEvent(new Event("stop"));
      }
    }
    w.MediaRecorder = ScriptedRecorder;
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        cancel: () => undefined,
        speak: (utterance: { text: string }) => w.__spoken.push(utterance.text),
      },
    });
    (window as unknown as { SpeechSynthesisUtterance: unknown }).SpeechSynthesisUtterance =
      class {
        public constructor(public text: string) {}
      };
  });
}

async function say(page: Page, utterance: string): Promise<void> {
  await page.evaluate((text) => {
    (window as unknown as { __utterances: string[] }).__utterances.push(text);
  }, utterance);
  await page.keyboard.down(PEDAL);
  await expect(page.getByText(/Listening/).first()).toBeVisible();
  await page.keyboard.up(PEDAL);
}

async function spoken(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __spoken: string[] }).__spoken);
}

test("a sterile step is completed with the pedal and voice alone", async ({ page }) => {
  await installMicrophoneAndSpeaker(page);
  await startReviewedSession(page);
  // From here on: no clicks and no typing. The page body has focus.
  await page.locator("body").focus();

  // 1. Measurement by voice -> a proposal that is not yet recorded.
  await say(page, "Jarvis, record the volume as 10 mL");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("10");

  // 2. Spoken read-back confirmation of the exact value.
  await say(page, "confirm 10 millilitres");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText(/volume 10 mL/i)).toBeVisible();

  // 3. Next step, answered from the approved protocol and spoken aloud.
  await say(page, "what is the next step");
  await expect.poll(async () => (await spoken(page)).join(" ")).toMatch(/Incubate/i);

  // 4-6. Voice-requested reagent check: automatic camera countdown, three
  // frames, deterministic consensus, and a spoken result.
  await say(page, "check reagent");
  await expect(page.getByText(/capturing in/i)).toBeVisible();
  await expect.poll(async () => (await spoken(page)).join(" "), { timeout: 15_000 }).toMatch(
    /Label check: match/i,
  );
  await expect(page.getByText(/MATCH/).first()).toBeVisible();
});
