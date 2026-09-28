import { useState } from "react";
import { Hand } from "lucide-react";

interface HandsFreeStartPromptProps {
  wakePhrase: string;
  /** Kiosk launch was requested but audio still needs a gesture (flag missing). */
  kioskFallback: boolean;
  onStart: () => Promise<void>;
}

/**
 * The single touch of a hands-free session. Browsers require one user gesture
 * before audio can play or be captured, so this is shown once, before gloving
 * up; it also asks for microphone and camera permission so both prompts appear
 * now rather than mid-protocol. In the documented kiosk launch mode the
 * gesture requirement is removed and this prompt is not shown at all.
 */
export function HandsFreeStartPrompt({ wakePhrase, kioskFallback, onStart }: HandsFreeStartPromptProps) {
  const [starting, setStarting] = useState(false);
  return (
    <section className="hands-free-start" aria-labelledby="hands-free-start-title">
      <h2 id="hands-free-start-title">Hands-free session</h2>
      <button
        className="button button--primary"
        type="button"
        disabled={starting}
        onClick={() => {
          setStarting(true);
          void onStart().finally(() => setStarting(false));
        }}
      >
        <Hand aria-hidden="true" /> Tap once to start hands-free
      </button>
      <p>
        This allows the microphone and camera and turns on listening for "{wakePhrase}". After
        this tap the whole bench workflow can be run by voice; the screen and the foot pedal
        remain available.
      </p>
      {kioskFallback ? (
        <p role="note">
          Kiosk mode was requested, but this browser still requires a tap before audio (the
          autoplay flag is not active), so one tap is needed.
        </p>
      ) : null}
    </section>
  );
}
