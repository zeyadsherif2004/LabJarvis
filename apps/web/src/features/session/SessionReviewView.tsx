import { useMemo, useState } from "react";
import { Cpu, Download, Edit3, ExternalLink, ShieldAlert } from "lucide-react";

import type { ActionResult } from "../../actions";
import type {
  ModelInferenceRecord,
  NotebookEntry,
  ReagentVerification,
  SessionResponse,
  UtteranceRecord,
} from "../../types";


interface SessionReviewViewProps {
  /** Included so a finished session can be summarised honestly. */
  session: SessionResponse;
  entries: NotebookEntry[];
  inferences: ModelInferenceRecord[];
  verifications?: ReagentVerification[];
  utterances?: UtteranceRecord[];
  /** True while the provenance records for this session are still arriving. */
  provenanceLoading?: boolean;
  jsonExportUrl: string;
  csvExportUrl: string;
  busy: boolean;
  onCorrect: (entryId: string, payload: Record<string, string>) => Promise<ActionResult>;
}

/** Human-readable agreement wording; never colour alone. */
const AGREEMENT_COPY: Record<string, string> = {
  identical: "Both readings were identical",
  equivalent: "The readings differed only in wording that cannot change the record",
  conflict: "The readings disagreed, so nothing was recorded from this utterance",
};

function inferenceSummary(inference: ModelInferenceRecord): string {
  const confidence =
    inference.confidence === null ? "" : `, confidence ${inference.confidence.toFixed(2)}`;
  return `${inference.adapter} / ${inference.model_name} (${inference.modality}, ${inference.status}, ${inference.latency_ms} ms${confidence})`;
}

export function SessionReviewView({
  session,
  entries,
  inferences,
  verifications = [],
  utterances = [],
  provenanceLoading = false,
  jsonExportUrl,
  csvExportUrl,
  busy,
  onCorrect,
}: SessionReviewViewProps) {
  const [filter, setFilter] = useState("all");
  const [correcting, setCorrecting] = useState<string | null>(null);
  const [correction, setCorrection] = useState("");
  const filtered = useMemo(
    () => entries.filter((entry) => filter === "all" || entry.entry_type === filter),
    [entries, filter],
  );
  const entryTypes = [...new Set(entries.map((entry) => entry.entry_type))].sort();
  const inferenceById = useMemo(
    () => new Map(inferences.map((inference) => [inference.id, inference])),
    [inferences],
  );
  const conflicted = utterances.filter((utterance) => utterance.agreement === "conflict");

  return (
    <main className="session-review" id="main-content">
      <header className="view-heading">
        <div>
          <h1>Session notebook</h1>
          {session.label ? <p>Run label: {session.label}</p> : null}
          <p>
            Original entries remain intact. Corrections append a linked replacement
            record.
          </p>
          <p className="session-summary">
            {session.state === "ABORTED"
              ? "This session was ended early. Steps after the last completed one were never performed."
              : session.state === "COMPLETED"
                ? "This session ran to the end of the protocol."
                : `This session is ${session.state.toLowerCase()}.`}{" "}
            {session.steps.filter((step) => step.state === "COMPLETED").length} of{" "}
            {session.steps.length} steps completed
            {session.steps.some((step) => step.state === "SKIPPED_WITH_REASON")
              ? `, ${session.steps.filter((step) => step.state === "SKIPPED_WITH_REASON").length} skipped with a recorded reason`
              : ""}
            .
          </p>
        </div>
        <div className="export-actions">
          <a className="button button--secondary" href={jsonExportUrl} download>
            <Download aria-hidden="true" /> JSON
          </a>
          <a className="button button--secondary" href={csvExportUrl} download>
            <Download aria-hidden="true" /> CSV
          </a>
        </div>
      </header>
      <label className="notebook-filter">
        Filter entries
        <select value={filter} onChange={(event) => setFilter(event.target.value)}>
          <option value="all">All entries</option>
          {entryTypes.map((type) => (
            <option value={type} key={type}>
              {type.replaceAll("_", " ")}
            </option>
          ))}
        </select>
      </label>
      <ol className="notebook-list">
        {filtered.map((entry) => {
          const inference =
            entry.model_inference_id === null
              ? undefined
              : inferenceById.get(entry.model_inference_id);
          return (
          <li key={entry.id}>
            <div className="notebook-entry-heading">
              <div>
                <strong>{entry.entry_type.replaceAll("_", " ")}</strong>
                <time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString()}</time>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label={`Correct ${entry.entry_type} entry`}
                onClick={() => {
                  setCorrecting(entry.id);
                  setCorrection(Object.values(entry.payload).join(" "));
                }}
              >
                <Edit3 aria-hidden="true" />
              </button>
            </div>
            <dl>
              {Object.entries(entry.payload).map(([key, value]) => (
                <div key={key}>
                  <dt>{key.replaceAll("_", " ")}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
            </dl>
            <footer>
              <span>Source: {entry.source.replaceAll("_", " ")}</span>
              <span>Correlation: {entry.correlation_id}</span>
              {entry.supersedes_entry_id ? (
                <span>
                  <ExternalLink aria-hidden="true" /> Corrects {entry.supersedes_entry_id}
                </span>
              ) : null}
              {inference === undefined ? null : (
                <span>
                  <Cpu aria-hidden="true" /> Model: {inferenceSummary(inference)}
                </span>
              )}
            </footer>
            {correcting === entry.id ? (
              <form
                className="correction-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void onCorrect(entry.id, { correction: correction.trim() }).then((result) => {
                    if (result.ok) {
                      setCorrecting(null);
                      setCorrection("");
                    }
                  });
                }}
              >
                <label>
                  Append correction
                  <textarea
                    value={correction}
                    rows={2}
                    required
                    onChange={(event) => setCorrection(event.target.value)}
                  />
                </label>
                <div>
                  <button className="button button--primary" type="submit" disabled={busy}>
                    Append correction
                  </button>
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => setCorrecting(null)}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            ) : null}
          </li>
          );
        })}
      </ol>

      {/* Provenance is part of the record, not a developer view. Reviewing a
          measurement months later means asking which model proposed it, how
          long it took, whether a second reading agreed, and who decided. */}
      <section className="notebook-provenance" aria-labelledby="provenance-title">
        <h2 id="provenance-title">
          <Cpu aria-hidden="true" /> Model provenance
        </h2>
        {provenanceLoading ? (
          <p aria-busy="true">Loading the provenance records for this session...</p>
        ) : inferences.length === 0 && utterances.length === 0 ? (
          <p>No model was involved in this session. Every entry came from a direct action.</p>
        ) : (
          <>
            <table className="provenance-table">
              <caption>
                Every model call made for this session, in order. Latency is measured
                around the adapter call itself.
              </caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Modality</th>
                  <th scope="col">Adapter and model</th>
                  <th scope="col">Status</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Latency</th>
                </tr>
              </thead>
              <tbody>
                {inferences.map((inference) => (
                  <tr key={inference.id}>
                    <td>
                      <time dateTime={inference.created_at}>
                        {new Date(inference.created_at).toLocaleTimeString()}
                      </time>
                    </td>
                    <td>{inference.modality.replaceAll("_", " ")}</td>
                    <td>
                      {inference.adapter} / <code>{inference.model_name}</code>
                    </td>
                    <td>{inference.status}</td>
                    <td>
                      {inference.confidence === null
                        ? "not reported"
                        : inference.confidence.toFixed(2)}
                    </td>
                    <td>{inference.latency_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {utterances.length === 0 ? null : (
              <>
                <h3>Spoken input cross-checks</h3>
                {conflicted.length > 0 ? (
                  <p className="provenance-conflict" role="note">
                    <ShieldAlert aria-hidden="true" /> {conflicted.length} utterance
                    {conflicted.length === 1 ? "" : "s"} were read two different ways and
                    were not acted on.
                  </p>
                ) : null}
                <ol className="provenance-utterances">
                  {utterances.map((utterance) => (
                    <li key={utterance.id}>
                      <strong>
                        {AGREEMENT_COPY[utterance.agreement] ?? utterance.agreement}
                      </strong>
                      <dl>
                        <div>
                          <dt>First reading</dt>
                          <dd>
                            {utterance.primary_model || "unnamed model"}
                            {utterance.realtime_transcript
                              ? `: "${utterance.realtime_transcript}"`
                              : " (transcript retention is off)"}
                          </dd>
                        </div>
                        <div>
                          <dt>Independent second reading</dt>
                          <dd>
                            {utterance.verification_model || "unnamed model"}
                            {utterance.verification_transcript
                              ? `: "${utterance.verification_transcript}"`
                              : " (transcript retention is off)"}
                          </dd>
                        </div>
                        <div>
                          <dt>Acted on</dt>
                          <dd>
                            {utterance.agreement === "conflict"
                              ? "Nothing. The operator was asked to repeat it."
                              : utterance.canonical_transcript ||
                                "recorded without retaining the text"}
                          </dd>
                        </div>
                        {utterance.reasons.length === 0 ? null : (
                          <div>
                            <dt>Differences found</dt>
                            <dd>{utterance.reasons.join("; ")}</dd>
                          </div>
                        )}
                      </dl>
                      <small>Correlation: {utterance.correlation_id}</small>
                    </li>
                  ))}
                </ol>
              </>
            )}

            {verifications.length === 0 ? null : (
              <>
                <h3>Label verification decisions</h3>
                <ol className="provenance-verifications">
                  {verifications.map((verification) => (
                    <li key={verification.id}>
                      <strong>
                        {verification.outcome} - decided {verification.decision.toLowerCase()}
                      </strong>
                      <p>
                        Expected <code>{verification.expected_label}</code>, read{" "}
                        <code>{verification.observed_label}</code>, similarity{" "}
                        {verification.score.toFixed(2)} against a{" "}
                        {verification.match_threshold.toFixed(2)} match threshold.
                      </p>
                      {verification.model_confidence === null ? null : (
                        <small>
                          Label model confidence {verification.model_confidence.toFixed(2)}
                        </small>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </>
        )}
      </section>
    </main>
  );
}
