import { useState } from "react";
import { BookOpenCheck, Send } from "lucide-react";

import type { ActionResult } from "../../actions";
import type { GroundedAnswer } from "../../types";

interface ProtocolQuestionPanelProps {
  busy: boolean;
  available?: boolean;
  onAsk: (question: string) => Promise<ActionResult<GroundedAnswer>>;
}

export function ProtocolQuestionPanel({
  busy,
  available = true,
  onAsk,
}: ProtocolQuestionPanelProps) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null);

  return (
    <section className="protocol-question" aria-labelledby="protocol-question-title">
      <header>
        <BookOpenCheck aria-hidden="true" />
        <div>
          <h2 id="protocol-question-title">Ask the approved protocol</h2>
          <p>Answers quote only the approved version and show their source.</p>
        </div>
      </header>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const submitted = question.trim();
          if (!submitted) return;
          void onAsk(submitted).then((result) => {
            if (!result.ok) return;
            setAnswer(result.value);
            setQuestion("");
          });
        }}
      >
        <label htmlFor="protocol-question-input">Question about this protocol</label>
        <div>
          <input
            id="protocol-question-input"
            value={question}
            maxLength={500}
            required
            disabled={busy || !available}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <button
            className="button button--secondary"
            type="submit"
            disabled={busy || !available || !question.trim()}
          >
            <Send aria-hidden="true" /> Ask
          </button>
        </div>
      </form>
      <output aria-live="polite">
        {answer === null ? null : answer.abstained ? (
          <p>The approved protocol does not answer this question.</p>
        ) : (
          <>
            <p>{answer.text}</p>
            <small>{answer.citation}</small>
          </>
        )}
      </output>
    </section>
  );
}
