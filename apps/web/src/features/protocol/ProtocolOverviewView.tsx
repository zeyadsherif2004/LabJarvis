import { useEffect, useState } from "react";
import { Archive, Clock3, Copy, FileText, FileCheck2, ShieldAlert } from "lucide-react";

import { StatusIndicator } from "../../components/StatusIndicator";
import { errorSentence } from "../../errors";
import type { ProtocolVersion, SessionSummary } from "../../types";


interface ProtocolOverviewViewProps {
  protocol: ProtocolVersion;
  /**
   * The session belonging to *this* approved version, or null when it has none.
   *
   * This used to be whichever session happened to be in memory, which had no
   * guaranteed relationship to the version named in the URL.
   */
  sessionSummary: SessionSummary | null;
  archivedAt: string | null;
  onOpenSession: () => void;
  onCopy: () => Promise<void>;
  onArchive: () => Promise<string>;
  onRestore: () => Promise<null>;
}

function durationLabel(seconds: number | null): string {
  if (seconds === null) return "No reviewed timer";
  if (seconds % 60 === 0) return `${seconds / 60} min reviewed timer`;
  return `${seconds} sec reviewed timer`;
}

export function ProtocolOverviewView({
  protocol,
  sessionSummary,
  archivedAt,
  onOpenSession,
  onCopy,
  onArchive,
  onRestore,
}: ProtocolOverviewViewProps) {
  const resumable = sessionSummary !== null && sessionSummary.resumable;
  const [archiveTimestamp, setArchiveTimestamp] = useState(archivedAt);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [lifecycleError, setLifecycleError] = useState<string | null>(null);

  useEffect(() => setArchiveTimestamp(archivedAt), [archivedAt]);

  async function runLifecycle(action: () => Promise<void>): Promise<void> {
    setLifecycleBusy(true);
    setLifecycleError(null);
    try {
      await action();
    } catch (caught) {
      setLifecycleError(errorSentence(caught));
    } finally {
      setLifecycleBusy(false);
    }
  }
  return (
    <main className="protocol-overview" id="main-content">
      <header className="view-heading">
        <div>
          <p className="eyebrow">Approved version {protocol.version_number}</p>
          <h1>Approved protocol</h1>
          <p>Read-only steps frozen for session use, with source references retained.</p>
        </div>
        {sessionSummary !== null ? (
          <StatusIndicator
            label={`Session ${sessionSummary.state.toLowerCase()}`}
            tone="ready"
          />
        ) : null}
      </header>
      <section className="protocol-lifecycle" aria-labelledby="protocol-lifecycle-title">
        <div>
          <h2 id="protocol-lifecycle-title">Protocol lifecycle</h2>
          <p>
            The approved version remains read-only. Copying creates a separate draft revision
            that can be reviewed without changing sessions already tied to this version.
          </p>
        </div>
        <div className="data-actions">
          <button
            className="button button--secondary"
            type="button"
            disabled={lifecycleBusy}
            onClick={() => void runLifecycle(onCopy)}
          >
            <Copy aria-hidden="true" /> Copy as new draft
          </button>
          {archiveTimestamp === null ? (
            <button
              className="button button--secondary"
              type="button"
              disabled={lifecycleBusy}
              onClick={() => void runLifecycle(async () => setArchiveTimestamp(await onArchive()))}
            >
              <Archive aria-hidden="true" /> Archive protocol
            </button>
          ) : (
            <button
              className="button button--secondary"
              type="button"
              disabled={lifecycleBusy}
              onClick={() => void runLifecycle(async () => setArchiveTimestamp(await onRestore()))}
            >
              <FileText aria-hidden="true" /> Restore protocol
            </button>
          )}
        </div>
        {archiveTimestamp !== null ? <p role="status">Archived {archiveTimestamp}</p> : null}
        {lifecycleError !== null ? <p role="alert">{lifecycleError}</p> : null}
      </section>
      <ol className="overview-step-list">
        {protocol.steps.map((step) => (
          <li key={step.id}>
            <span className="overview-number">{step.number}</span>
            <div>
              <div className="overview-step-heading">
                <h2>{step.instruction}</h2>
              </div>
              <div className="overview-meta">
                <span><FileCheck2 aria-hidden="true" /> Page {step.source.page}{step.source.section ? ` - ${step.source.section}` : ""}</span>
                <span><Clock3 aria-hidden="true" /> {durationLabel(step.duration_seconds)}</span>
              </div>
              <p className="overview-excerpt">{step.source.excerpt}</p>
              {step.requirements.length > 0 ? (
                <dl className="overview-requirements">
                  {step.requirements.map((requirement) => (
                    <div key={requirement.requirement_id}>
                      <dt>Required material</dt>
                      <dd>{[requirement.quantity, requirement.unit, requirement.name].filter(Boolean).join(" ")}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {step.safety_notes.map((note) => (
                <p className="overview-safety" key={note}><ShieldAlert aria-hidden="true" /> {note}</p>
              ))}
            </div>
          </li>
        ))}
      </ol>
      {sessionSummary === null ? null : (
        <button
          className="button button--primary overview-return"
          type="button"
          onClick={onOpenSession}
        >
          {resumable ? "Return to sterile mode" : "Open this protocol's notebook"}
        </button>
      )}
    </main>
  );
}
