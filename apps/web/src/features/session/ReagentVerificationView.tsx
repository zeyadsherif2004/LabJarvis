import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Camera, CheckCircle2, FlaskConical, HelpCircle } from "lucide-react";

import type { ActionResult } from "../../actions";
import { captureAimedFrame } from "../../media/labelFrame";
import {
  loadLocalCaptureMode,
  saveLocalCaptureMode,
  type LocalCaptureMode,
} from "../../media/localCaptureMode";
import { evaluateScan, LIVE_SCAN_RULE, type ProbeReading, type ScanRule } from "../../media/liveScan";
import type {
  CaptureMetadata,
  LabelProbe,
  ProtocolRequirement,
  ReadinessResponse,
  ReagentVerification,
  RequirementProgress,
} from "../../types";


interface ReagentVerificationViewProps {
  /** The reagents the active step actually requires. May be empty. */
  requirements: ProtocolRequirement[];
  progress: RequirementProgress[];
  result: ReagentVerification | null;
  mode: ReadinessResponse["mode"];
  busy: boolean;
  onVerify: (image: Blob, requirementId: string) => Promise<ActionResult>;
  onConfirm: (
    accepted: boolean,
    correctedObservedLabel: string | null,
  ) => Promise<ActionResult>;
  /** One or more frames of one label; multi-frame checks use unanimous consensus. */
  onVerifyFrames?:
    | ((frames: Blob[], requirementId: string, capture?: CaptureMetadata) => Promise<ActionResult>)
    | undefined;
  /**
   * Live "scan until stable" mode: when set, a hands-free check probes frames
   * with the local OCR until `liveScan` says the reading is stable, then submits
   * the stable frames as one ordinary (pending) reading.
   */
  liveScan?: ScanRule | null;
  /** Remote Vision sends one deliberately timed frame instead of live probes. */
  hostedSingleCapture?: boolean;
  onProbe?: ((frame: Blob, requirementId: string) => Promise<LabelProbe | null>) | undefined;
  /**
   * Incremented when a spoken "check reagent" asks for a hands-free capture:
   * the camera opens and the selected local/hosted capture runs without touch.
   */
  autoCaptureRequest?: number | undefined;
  /**
   * The requirement a spoken "check the ethanol" / "check reagent two" chose,
   * resolved by the server. The capture uses it instead of the radio selection;
   * without it (several reagents, none named) no capture starts.
   */
  autoCaptureRequirementId?: string | null | undefined;
  speak?: ((text: string) => void) | undefined;
  isSpeaking?: (() => boolean) | undefined;
  countdownMs?: number;
  /** Voice-only mode: the exact reply that answers the pending reading. */
  voiceOnlyHint?: string | null;
}

export const HANDS_FREE_FRAMES = 3;
const FRAME_INTERVAL_MS = 400;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function describeRequirement(requirement: ProtocolRequirement): string {
  const quantity =
    requirement.quantity !== null && requirement.unit !== null
      ? `${requirement.quantity} ${requirement.unit} `
      : "";
  return `${quantity}${requirement.name}`;
}

const resultCopy = {
  MATCH: "Visible label agrees with the reviewed protocol.",
  MISMATCH: "Do not proceed. The visible label does not match the reviewed protocol.",
  UNCERTAIN: "Do not proceed until a person verifies the visible label.",
};

const progressCopy: Record<RequirementProgress["status"], string> = {
  unverified: "label not reviewed",
  pending_review: "label review pending",
  confirmed: "label review confirmed",
  not_matching: "reviewed - label does NOT match",
  rejected: "label review rejected",
  stale: "label review stale",
};

/**
 * Development-only seam for the zero-touch browser test: which printed label
 * the operator is holding up in the fake profile (the physical act of picking
 * up a different bottle). Ignored outside Vite development builds.
 */
function heldLabel(fixture: string): string {
  if ((import.meta as { env?: { DEV?: boolean } }).env?.DEV !== true) return fixture;
  const held = (globalThis as { __labjarvisHeldLabel?: unknown }).__labjarvisHeldLabel;
  return typeof held === "string" && held.trim() !== "" ? held : fixture;
}

export function ReagentVerificationView({
  requirements,
  progress,
  result,
  mode,
  busy,
  onVerify,
  onConfirm,
  onVerifyFrames,
  autoCaptureRequest = 0,
  autoCaptureRequirementId = null,
  speak,
  countdownMs = 1000,
  voiceOnlyHint = null,
  liveScan = null,
  hostedSingleCapture = false,
  onProbe,
  isSpeaking,
}: ReagentVerificationViewProps) {
  const [countdown, setCountdown] = useState<number | null>(null);
  const [scanStatus, setScanStatus] = useState<string | null>(null);
  const [localCaptureMode, setLocalCaptureMode] = useState<LocalCaptureMode>(loadLocalCaptureMode);
  const scanCancelled = useRef(false);
  const handledRequest = useRef(autoCaptureRequest);
  const requestedRequirement = useRef<string | null>(null);
  const [fixture, setFixture] = useState("ethanol");
  const [selectedRequirementId, setSelectedRequirementId] = useState(
    requirements[0]?.requirement_id ?? "",
  );
  const [cameraOpen, setCameraOpen] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [observedText, setObservedText] = useState(result?.observed_label ?? "");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const tone = result?.outcome.toLowerCase() ?? "idle";
  const Icon =
    result?.outcome === "MATCH"
      ? CheckCircle2
      : result?.outcome === "MISMATCH"
        ? AlertTriangle
        : HelpCircle;
  const useLiveScan = !hostedSingleCapture && localCaptureMode === "live"
    && liveScan !== null && onProbe !== undefined;
  const singleCapture = hostedSingleCapture
    || (liveScan !== null && localCaptureMode === "timed_single");

  useEffect(
    () => () => {
      scanCancelled.current = true;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    },
    [],
  );

  useEffect(() => {
    setObservedText(result?.observed_label ?? "");
  }, [result?.id, result?.observed_label]);

  const requirementIds = requirements.map((item) => item.requirement_id).join(",");
  useEffect(() => {
    // Advancing to a step with different reagents must not leave a stale
    // selection pointing at a requirement that is no longer on screen.
    const ids = requirementIds ? requirementIds.split(",") : [];
    setSelectedRequirementId((current) =>
      ids.includes(current) ? current : (ids[0] ?? ""),
    );
  }, [requirementIds]);


  const selected =
    requirements.find((item) => item.requirement_id === selectedRequirementId) ??
    requirements[0];
  const progressByRequirement = new Map(
    progress.map((item) => [item.requirement_id, item.status]),
  );

  function closeCamera(): void {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current !== null) videoRef.current.srcObject = null;
    setCameraOpen(false);
  }

  async function openCamera(): Promise<void> {
    setCameraError(null);
    if (navigator.mediaDevices?.getUserMedia === undefined) {
      setCameraError("Camera capture is unavailable. Choose an existing image instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: "environment" } },
      });
      closeCamera();
      streamRef.current = stream;
      if (videoRef.current !== null) {
        videoRef.current.srcObject = stream;
        void videoRef.current.play().catch(() => undefined);
      }
      setCameraOpen(true);
    } catch (error) {
      const permissionDenied = error instanceof DOMException && error.name === "NotAllowedError";
      setCameraError(
        permissionDenied
          ? "Camera permission denied. Choose an existing image instead."
          : "Camera capture failed. Choose an existing image instead.",
      );
    }
  }

  async function grabFrame(): Promise<Blob | null> {
    const video = videoRef.current;
    if (video === null || streamRef.current === null) return null;
    // Only the aim box is read; see media/labelFrame.ts for why.
    return captureAimedFrame(video);
  }

  /**
   * Scan until stable: probe a frame every `intervalMs` (nothing is recorded),
   * keep the last N frames, and submit them once the rule reports a stable
   * reading. Stability authorises nothing - the result is a pending reading
   * that still needs a spoken or on-screen confirmation.
   */
  async function runLiveScan(rule: ScanRule): Promise<void> {
    if (onVerifyFrames === undefined || onProbe === undefined) return;
    const requirementId =
      requestedRequirement.current || selectedRequirementId || requirements[0]?.requirement_id || "";
    const target = requirements.find((item) => item.requirement_id === requirementId) ?? selected;
    speak?.(`Hold the ${target?.name ?? "label"} label to the camera. I will read it when it is steady.`);
    if (mode !== "fake") {
      await openCamera();
      if (streamRef.current === null) return;
    }
    scanCancelled.current = false;
    setCameraError(null);
    setScanStatus("Scanning the label...");
    const started = performance.now();
    const readings: ProbeReading[] = [];
    // frames[i] is the image behind readings[i], so the agreeing frames can be
    // submitted exactly (bounded by the rule's frame budget).
    const frames: Blob[] = [];
    let failedProbes = 0;
    let lastPrompt = "";
    let lastPromptAt = 0;
    try {
      for (;;) {
        if (scanCancelled.current) {
          setScanStatus(null);
          return;
        }
        const frame =
          mode === "fake"
            ? new Blob([`FAKE:${heldLabel(fixture)}`], { type: "image/jpeg" })
            : await grabFrame();
        if (frame === null) {
          setCameraError("No frame could be captured. Say check reagent to try again.");
          return;
        }
        const probe = await onProbe(frame, requirementId);
        if (probe === null) {
          // A failed request (the reader busy or unreachable) says nothing
          // about the label, so it is skipped rather than counted as an
          // unreadable frame - counting it used to break a steady scan.
          failedProbes += 1;
          if (performance.now() - started >= rule.timeoutMs) {
            closeCamera();
            const prompt =
              readings.length === 0
                ? "The label reader did not respond. Check again in a moment."
                : "I could not get a steady reading. Check again.";
            speak?.(prompt);
            setCameraError(`${prompt} Nothing was recorded.`);
            return;
          }
          await wait(Math.min(rule.intervalMs, 250) * Math.min(failedProbes, 4));
          continue;
        }
        failedProbes = 0;
        readings.push({ outcome: probe.outcome, normalised: probe.observed_normalised });
        frames.push(frame);
        const elapsed = performance.now() - started;
        const state = evaluateScan(readings, elapsed, rule);
        if (state.status === "stable") {
          setScanStatus(`Steady reading after ${state.framesExamined} frames - comparing...`);
          closeCamera();
          const agreeing = state.frameIndexes
            .map((index) => frames[index])
            .filter((item): item is Blob => item !== undefined);
          await onVerifyFrames(agreeing, requirementId, {
            mode: "live_scan",
            scanRule: LIVE_SCAN_RULE,
            framesExamined: state.framesExamined,
            elapsedMs: elapsed,
          });
          return;
        }
        if (state.status === "timeout") {
          closeCamera();
          speak?.(state.prompt);
          setCameraError(`${state.prompt} Nothing was recorded.`);
          return;
        }
        setScanStatus(
          `Scanning: ${state.framesExamined} frame${state.framesExamined === 1 ? "" : "s"} read` +
            (state.prompt ? ` - ${state.prompt}` : ""),
        );
        const now = performance.now();
        if (state.prompt && (state.prompt !== lastPrompt || now - lastPromptAt > 4000)) {
          speak?.(state.prompt);
          lastPrompt = state.prompt;
          lastPromptAt = now;
        }
        await wait(rule.intervalMs);
      }
    } finally {
      if (streamRef.current !== null) closeCamera();
      // The stable-reading status used to persist after the reading was
      // submitted, which silently blocked every later hands-free check in the
      // same step (found by the zero-touch E2E, 2026-09-27). The pending
      // reading itself is now on screen, so the status is cleared.
      setScanStatus(null);
    }
  }

  async function runHandsFreeCapture(): Promise<void> {
    if (onVerifyFrames === undefined || busy || countdown !== null || scanStatus !== null) return;
    if (useLiveScan && liveScan !== null) {
      await runLiveScan(liveScan);
      return;
    }
    const frameCount = singleCapture ? 1 : HANDS_FREE_FRAMES;
    const requirementId =
      requestedRequirement.current || selectedRequirementId || requirements[0]?.requirement_id || "";
    const target = requirements.find((item) => item.requirement_id === requirementId) ?? selected;
    speak?.(
      singleCapture
        ? `Hold the ${target?.name ?? "label"} inside the box; capturing in three, two, one.`
        : `Hold the ${target?.name ?? "label"} label to the camera. Capturing in three.`,
    );
    if (mode !== "fake") {
      await openCamera();
      if (streamRef.current === null) return;
    }
    for (let remaining = 3; remaining >= 1; remaining -= 1) {
      setCountdown(remaining);
      await wait(countdownMs);
    }
    if (singleCapture && isSpeaking !== undefined) {
      const audioDeadline = performance.now() + 10_000;
      while (isSpeaking() && performance.now() < audioDeadline) await wait(100);
      if (isSpeaking()) {
        closeCamera();
        setCountdown(null);
        setCameraError("The spoken countdown did not finish. No image was sent; try again.");
        return;
      }
    }
    setCountdown(0);
    const frames: Blob[] = [];
    for (let index = 0; index < frameCount; index += 1) {
      if (mode === "fake") {
        frames.push(new Blob([`FAKE:${heldLabel(fixture)}`], { type: "image/jpeg" }));
      } else {
        const frame = await grabFrame();
        if (frame !== null) frames.push(frame);
      }
      if (index < frameCount - 1) await wait(FRAME_INTERVAL_MS);
    }
    closeCamera();
    setCountdown(null);
    if (frames.length === 0) {
      setCameraError("No frame could be captured. Say check reagent to try again.");
      return;
    }
    await onVerifyFrames(frames, requirementId, { mode: singleCapture ? "single" : "countdown" });
  }

  useEffect(() => {
    if (autoCaptureRequest === handledRequest.current) return;
    handledRequest.current = autoCaptureRequest;
    const requested =
      autoCaptureRequirementId !== null &&
      requirements.some((item) => item.requirement_id === autoCaptureRequirementId)
        ? autoCaptureRequirementId
        : null;
    // Never fall back to a default silently when the step has several reagents.
    if (requested === null && requirements.length > 1) return;
    requestedRequirement.current = requested;
    if (requested !== null) setSelectedRequirementId(requested);
    if (requirements.length > 0) void runHandsFreeCapture();
    // The request counter is the only trigger; the capture reads current props.
  }, [autoCaptureRequest]);

  async function captureFrame(): Promise<void> {
    const video = videoRef.current;
    if (video === null || streamRef.current === null) return;
    const image = await captureAimedFrame(video);
    if (image === null) {
      setCameraError("Camera frame could not be captured. Choose an existing image instead.");
      return;
    }
    closeCamera();
    await onVerify(image, selectedRequirementId);
  }
  // A step that lists no reagent has nothing to verify. Rendering the panel
  // anyway invited the operator to photograph a bottle that the protocol never
  // asked for, and produced a verification record with no expected value.
  if (requirements.length === 0) return null;

  return (
    <section className="verification-section" aria-labelledby="verification-title">
      <header>
        <FlaskConical aria-hidden="true" />
        <div>
          <h2 id="verification-title">Reagent verification</h2>
          <p>
            Compare visible label text with the active reviewed step. Label matching
            does not verify a bottle's contents or its safety.
          </p>
        </div>
      </header>

      {requirements.length > 1 ? (
        <fieldset className="requirement-picker">
          <legend>
            This step needs {requirements.length} reagents - choose which one you are
            holding
          </legend>
          {requirements.map((requirement) => (
            <label key={requirement.requirement_id}>
              <input
                type="radio"
                name="reagent-requirement"
                value={requirement.requirement_id}
                checked={requirement.requirement_id === selected?.requirement_id}
                disabled={busy}
                onChange={() => {
                  requestedRequirement.current = null;
                  setSelectedRequirementId(requirement.requirement_id);
                }}
              />
              <span>{describeRequirement(requirement)}</span>
              {(() => {
                const status =
                  progressByRequirement.get(requirement.requirement_id) ?? "unverified";
                return (
                  <small
                    className={
                      status === "confirmed" ? "requirement-done" : "requirement-todo"
                    }
                  >
                    {progressCopy[status]}
                  </small>
                );
              })()}
            </label>
          ))}
        </fieldset>
      ) : (
        <p className="requirement-single">
          Expected:{" "}
          <strong>{selected ? describeRequirement(selected) : ""}</strong>
        </p>
      )}

      {!hostedSingleCapture && liveScan !== null && onVerifyFrames !== undefined ? (
        <fieldset className="local-capture-picker">
          <legend>Local camera check</legend>
          <label>
            <input
              type="radio"
              name="local-capture-mode"
              value="live"
              checked={localCaptureMode === "live"}
              disabled={busy || countdown !== null || scanStatus !== null}
              onChange={() => {
                setLocalCaptureMode("live");
                saveLocalCaptureMode("live");
              }}
            />
            <span>Live scan — compare several readings (default)</span>
          </label>
          <label>
            <input
              type="radio"
              name="local-capture-mode"
              value="timed_single"
              checked={localCaptureMode === "timed_single"}
              disabled={busy || countdown !== null || scanStatus !== null}
              onChange={() => {
                setLocalCaptureMode("timed_single");
                saveLocalCaptureMode("timed_single");
              }}
            />
            <span>Timed single frame — position the label during a spoken countdown</span>
          </label>
          <p>One frame is faster but cannot reveal a changing OCR reading. Inspect the physical label before confirming.</p>
        </fieldset>
      ) : null}

      {mode === "fake" ? (
        <div className="fake-verification-controls">
          <label>
            Development label fixture
            <select value={fixture} onChange={(event) => setFixture(event.target.value)}>
              <option value="ethanol">Ethanol - expected match</option>
              <option value="methanol">Methanol - expected mismatch</option>
              <option value="ethano1">Ethano1 - expected uncertain</option>
            </select>
          </label>
          <button
            className="button button--secondary"
            type="button"
            disabled={busy}
            onClick={() =>
              void onVerify(
                new Blob([`FAKE:${heldLabel(fixture)}`], { type: "image/jpeg" }),
                selected?.requirement_id ?? "",
              )
            }
          >
            Verify visible label
          </button>
        </div>
      ) : (
        <div className="camera-controls">
          <div className="camera-frame" hidden={!cameraOpen}>
            <video
              ref={videoRef}
              className="camera-preview"
              aria-label="Live reagent label camera preview"
              autoPlay
              muted
              playsInline
            />
            <div className="camera-aim-box" aria-hidden="true">
              <span>Hold the label inside this box</span>
            </div>
          </div>
          {cameraOpen ? (
            <p className="camera-aim-hint">
              Only the text inside the box is read. Fill most of the box with the label and keep
              other writing out of it.
            </p>
          ) : null}
          <div className="camera-actions">
            {cameraOpen ? (
              <>
                <button
                  className="button button--primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void captureFrame()}
                >
                  <Camera aria-hidden="true" /> Capture visible label
                </button>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={closeCamera}
                >
                  Close camera
                </button>
              </>
            ) : (
              <button
                className="button button--secondary"
                type="button"
                disabled={busy}
                onClick={() => void openCamera()}
              >
                <Camera aria-hidden="true" /> Open camera
              </button>
            )}
            <label className="camera-input">
              Choose existing label image
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file !== undefined) void onVerify(file, selectedRequirementId);
                }}
              />
            </label>
          </div>
          <p className="camera-privacy">
            {hostedSingleCapture
              ? "Captured label images are sent to the configured Vision provider. LabJarvis discards raw images after processing by default."
              : "Camera frames stay local and raw images are discarded after processing by default."}
          </p>
        </div>
      )}

      {cameraError !== null ? (
        <p className="camera-error" role="alert">
          {cameraError}
        </p>
      ) : null}
      {countdown !== null ? (
        <p className="capture-countdown" role="status" aria-live="assertive">
          {countdown > 0
            ? `Hold the label steady - capturing in ${countdown}`
            : `Capturing ${singleCapture ? 1 : HANDS_FREE_FRAMES} frame${singleCapture ? "" : "s"}...`}
        </p>
      ) : null}
      {scanStatus !== null ? (
        <p className="capture-countdown" role="status" aria-live="polite">
          {scanStatus}
        </p>
      ) : null}
      {onVerifyFrames !== undefined ? (
        <div className="camera-actions">
          <button
            className="button button--secondary"
            type="button"
            disabled={busy || countdown !== null || scanStatus !== null}
            onClick={() => void runHandsFreeCapture()}
          >
            <Camera aria-hidden="true" />{" "}
            {useLiveScan
              ? result === null
                ? "Scan label until steady"
                : "Scan again"
              : singleCapture
                ? result === null
                  ? "Check label (1 frame)"
                  : "Recapture (1 frame)"
              : result === null
                ? "Check label (3 frames)"
                : "Recapture (3 frames)"}
          </button>
          {scanStatus !== null && !scanStatus.startsWith("Steady") ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => {
                scanCancelled.current = true;
              }}
            >
              Stop scanning
            </button>
          ) : null}
        </div>
      ) : null}

      {result !== null ? (
        <div
          className={`verification-result verification-result--${tone}`}
          role={result.outcome === "MATCH" ? "status" : "alert"}
        >
          <Icon aria-hidden="true" />
          <div>
            <strong>
              {result.outcome} · {result.decision}
            </strong>
            <p>{resultCopy[result.outcome]}</p>
            <small>
              Expected {result.expected_label}; observed {result.observed_label}; comparison score{" "}
              {result.score.toFixed(2)}.
            </small>
          </div>
        </div>
      ) : null}
      {voiceOnlyHint !== null ? (
        <p className="voice-only-hint" role="note">
          {voiceOnlyHint}
        </p>
      ) : null}
      {result?.decision === "PENDING" ? (
        <form
          className="verification-confirmation"
          onSubmit={(event) => {
            event.preventDefault();
            const corrected = observedText.trim();
            if (!corrected) return;
            void onConfirm(
              true,
              corrected === result.observed_label ? null : corrected,
            );
          }}
        >
          <label>
            Confirm or correct visible OCR text
            <input
              value={observedText}
              maxLength={250}
              required
              disabled={busy}
              onChange={(event) => setObservedText(event.target.value)}
            />
          </label>
          <div>
            <button className="button button--confirm" type="submit" disabled={busy}>
              Confirm visible reading
            </button>
            <button
              className="button button--secondary"
              type="button"
              disabled={busy}
              onClick={() => void onConfirm(false, null)}
            >
              Reject OCR proposal
            </button>
          </div>
        </form>
      ) : null}
      <p className="verification-boundary">
        LabJarvis compares visible text only. It does not identify contents or assess sterility.
      </p>
    </section>
  );
}
