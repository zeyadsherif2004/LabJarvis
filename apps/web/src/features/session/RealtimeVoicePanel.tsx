import {
  Mic,
  MicOff,
  RadioTower,
  RotateCcw,
  ShieldAlert,
  Square,
  Wifi,
  WifiOff,
} from "lucide-react";

import type { RealtimeStatus } from "../../types";

interface RealtimeVoicePanelProps {
  enabled: boolean;
  status: RealtimeStatus;
  consented: boolean;
  muted: boolean;
  modelName: string;
  provider: string;
  detail: string | null;
  /** The last sentence LabJarvis was asked to speak, shown as a text alternative. */
  spoken: string | null;
  reconnectAttempt: number;
  maxReconnectAttempts: number;
  onConsent: () => void;
  onStop: () => void;
  onRetry: () => void;
  onMutedChange: (muted: boolean) => void;
}

const STATUS_COPY: Record<RealtimeStatus, string> = {
  idle: "Hands-free voice is off.",
  requesting_permission: "Waiting for microphone permission...",
  connecting: "Connecting to the voice service...",
  listening: "Listening. Speak when you are ready.",
  processing: "Working on that...",
  speaking: "Speaking. Talk over me to interrupt.",
  awaiting_confirmation: "Waiting for you to confirm out loud. Nothing is recorded yet.",
  capturing_confirmation: "Listening for your confirmation now.",
  reconnecting: "Connection lost. Rebuilding the voice session...",
  muted: "Microphone muted. Typed control still works.",
  stopped: "Hands-free voice stopped.",
  error: "Hands-free voice is unavailable.",
};

const LIVE_STATUSES: RealtimeStatus[] = [
  "listening",
  "processing",
  "speaking",
  "awaiting_confirmation",
  "capturing_confirmation",
];

export function RealtimeVoicePanel(props: RealtimeVoicePanelProps) {
  const {
    enabled,
    status,
    consented,
    muted,
    modelName,
    provider,
    detail,
    spoken,
    reconnectAttempt,
    maxReconnectAttempts,
    onConsent,
    onStop,
    onRetry,
    onMutedChange,
  } = props;

  if (!enabled) {
    return (
      <section className="realtime-panel realtime-panel--off" aria-labelledby="realtime-title">
        <h2 id="realtime-title">
          <RadioTower aria-hidden="true" /> Hands-free voice
        </h2>
        <p>
          Hands-free voice is not enabled on this installation. Push-to-talk and typed
          commands remain fully available.
        </p>
      </section>
    );
  }

  const live = LIVE_STATUSES.includes(status);

  return (
    <section
      className={`realtime-panel realtime-panel--${status}`}
      aria-labelledby="realtime-title"
    >
      <h2 id="realtime-title">
        <RadioTower aria-hidden="true" /> Hands-free voice
      </h2>

      {!consented ? (
        <div className="realtime-consent">
          <ShieldAlert aria-hidden="true" />
          <div>
            <strong>Your microphone will stream to a hosted service</strong>
            <p>
              While hands-free mode is on, audio is sent continuously to {provider || "the configured provider"}{" "}
              using model <code>{modelName || "unconfigured"}</code> so it can respond
              without you touching anything. Audio is not stored by LabJarvis. Voice can
              only propose actions; recording anything still needs you to say it back.
            </p>
            <p>You can stop it at any time, and typed control always stays available.</p>
          </div>
          <button className="button button--primary" type="button" onClick={onConsent}>
            <Mic aria-hidden="true" /> Turn on hands-free voice
          </button>
        </div>
      ) : (
        <>
          {/* A visible indicator whenever the microphone is actually live. */}
          <p className="realtime-status" role="status">
            <span
              className={live && !muted ? "realtime-dot realtime-dot--live" : "realtime-dot"}
              aria-hidden="true"
            />
            {muted ? STATUS_COPY.muted : STATUS_COPY[status]}
          </p>

          {status === "reconnecting" ? (
            <p className="realtime-reconnect" role="status">
              <WifiOff aria-hidden="true" /> Attempt {reconnectAttempt} of{" "}
              {maxReconnectAttempts}. The session is re-read from the database before
              anything continues, and nothing unconfirmed is repeated.
            </p>
          ) : null}

          {/* Everything spoken is also written down, so the interface is usable
              with the sound off, in a noisy laboratory, or by someone who did
              not catch the sentence. */}
          {spoken !== null ? (
            <p className="realtime-spoken">
              <strong>LabJarvis said:</strong> {spoken}
            </p>
          ) : null}

          {status === "error" && detail !== null ? (
            <p className="realtime-error" role="alert">
              {detail}
            </p>
          ) : null}
          {status === "awaiting_confirmation" && detail !== null ? (
            <p className="realtime-error" role="alert">
              {detail}
            </p>
          ) : null}

          <div className="realtime-controls">
            <button
              className="button button--secondary"
              type="button"
              aria-pressed={muted}
              onClick={() => onMutedChange(!muted)}
            >
              {muted ? <Mic aria-hidden="true" /> : <MicOff aria-hidden="true" />}
              {muted ? "Unmute microphone" : "Mute microphone"}
            </button>
            {status === "error" ? (
              <button className="button button--secondary" type="button" onClick={onRetry}>
                <RotateCcw aria-hidden="true" /> Reconnect hands-free voice
              </button>
            ) : null}
            <button className="button button--danger" type="button" onClick={onStop}>
              <Square aria-hidden="true" /> Stop hands-free voice
            </button>
          </div>

          <p className="realtime-provenance">
            <Wifi aria-hidden="true" /> {provider} - <code>{modelName}</code>
          </p>
        </>
      )}
    </section>
  );
}
