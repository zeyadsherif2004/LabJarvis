import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  FileText,
  FlaskConical,
  Loader2,
  RotateCcw,
  Search,
  Upload,
} from "lucide-react";

import type { ProtocolSummary, SessionSummary } from "../../types";
import type { DashboardFilters } from "./useDashboardDiscovery";

interface DashboardViewProps {
  protocols: ProtocolSummary[];
  sessions: SessionSummary[];
  loading: boolean;
  authoritative: boolean;
  error: string | null;
  onSearch: (filters: DashboardFilters) => void;
  onOpenProtocol: (summary: ProtocolSummary) => void;
  onResumeSession: (summary: SessionSummary) => void;
  onImport: () => void;
  onRetry: () => void;
}

function relativeTime(isoTimestamp: string): string {
  const then = new Date(isoTimestamp).getTime();
  if (Number.isNaN(then)) return "unknown";
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function stateLabel(state: SessionSummary["state"]): string {
  return state.charAt(0) + state.slice(1).toLowerCase();
}

export function DashboardView(props: DashboardViewProps) {
  const {
    protocols,
    sessions,
    loading,
    authoritative,
    error,
    onSearch,
    onOpenProtocol,
    onResumeSession,
    onImport,
    onRetry,
  } = props;
  const [query, setQuery] = useState("");
  const [label, setLabel] = useState("");
  const [protocolVersionId, setProtocolVersionId] = useState("");
  const [state, setState] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [archived, setArchived] = useState(false);
  const initialSearch = useRef(true);

  // Debounced so typing does not issue one request per keystroke.
  useEffect(() => {
    if (initialSearch.current) {
      initialSearch.current = false;
      return;
    }
    const handle = window.setTimeout(
      () => onSearch({ query: query.trim(), label: label.trim(), protocolVersionId: protocolVersionId.trim(), state, createdFrom, createdTo, archived }),
      250,
    );
    return () => window.clearTimeout(handle);
  }, [archived, createdFrom, createdTo, label, onSearch, protocolVersionId, query, state]);

  const resumable = sessions.filter((session) => session.resumable);
  const finished = sessions.filter((session) => !session.resumable);

  return (
    <main className="dashboard" id="main-content">
      <header className="dashboard-header">
        <div>
          <h1>LabJarvis</h1>
          <p>
            Import a protocol, review it, then run it hands-free. Every recorded action
            is confirmed by you before it is written.
          </p>
        </div>
        <button className="button button--primary" type="button" onClick={onImport}>
          <Upload aria-hidden="true" /> Import a protocol
        </button>
      </header>

      <div className="dashboard-search">
        <label htmlFor="dashboard-search-input">
          <Search aria-hidden="true" /> Search protocols and sessions
        </label>
        <input
          id="dashboard-search-input"
          type="search"
          value={query}
          placeholder="Search by protocol title"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <fieldset className="dashboard-filters">
        <legend>Filter records</legend>
        <label>
          Session state
          <select value={state} onChange={(event) => setState(event.target.value)}>
            <option value="">Any state</option>
            <option value="READY">Ready</option>
            <option value="ACTIVE">Active</option>
            <option value="PAUSED">Paused</option>
            <option value="COMPLETED">Completed</option>
            <option value="ABORTED">Aborted</option>
          </select>
        </label>
        <label>
          Session label
          <input value={label} onChange={(event) => setLabel(event.target.value)} />
        </label>
        <label>
          Protocol version ID
          <input value={protocolVersionId} onChange={(event) => setProtocolVersionId(event.target.value)} />
        </label>
        <label>
          Created from
          <input type="date" value={createdFrom} onChange={(event) => setCreatedFrom(event.target.value)} />
        </label>
        <label>
          Created to
          <input type="date" value={createdTo} onChange={(event) => setCreatedTo(event.target.value)} />
        </label>
        <label className="dashboard-filter-check">
          <input type="checkbox" checked={archived} onChange={(event) => setArchived(event.target.checked)} />
          Show archived protocols
        </label>
      </fieldset>

      {error !== null ? (
        <div className="dashboard-error" role="alert">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>Could not load your work</strong>
            <p>{error}</p>
          </div>
          <button className="button button--secondary" type="button" onClick={onRetry}>
            <RotateCcw aria-hidden="true" /> Retry
          </button>
        </div>
      ) : null}

      {loading ? (
        <p className="dashboard-loading" aria-busy="true">
          <Loader2 aria-hidden="true" />{" "}
          {protocols.length > 0 || sessions.length > 0
            ? "Refreshing results..."
            : "Loading your protocols and sessions..."}
        </p>
      ) : null}

      <section aria-labelledby="resumable-title">
        <h2 id="resumable-title">Continue where you left off</h2>
        {resumable.length === 0 ? (
          <p className="dashboard-empty">
            {loading
              ? "Checking for sessions in progress..."
              : "No session is in progress. Approve a protocol to start one."}
          </p>
        ) : (
          <ul className="dashboard-list">
            {resumable.map((session) => (
              <li key={session.session_id}>
                <button
                  type="button"
                  disabled={!authoritative}
                  onClick={() => onResumeSession(session)}
                >
                  <span className="dashboard-list-icon">
                    <FlaskConical aria-hidden="true" />
                  </span>
                  <span className="dashboard-list-body">
                    <strong>{session.protocol_title}</strong>
                    {session.label ? <small>Label: {session.label}</small> : null}
                    <small>
                      {stateLabel(session.state)}
                      {session.active_step_number !== null
                        ? ` - on step ${session.active_step_number} of ${session.step_count}`
                        : ` - ${session.completed_step_count} of ${session.step_count} steps done`}
                    </small>
                    <small>Started {relativeTime(session.started_at ?? session.created_at)}</small>
                  </span>
                  <span className="dashboard-list-action">
                    Resume <ArrowRight aria-hidden="true" />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="protocols-title">
        <h2 id="protocols-title">Protocols</h2>
        {protocols.length === 0 ? (
          <p className="dashboard-empty">
            {loading
              ? "Checking for imported protocols..."
              : "Nothing imported yet. Import a PDF or DOCX protocol to begin."}
          </p>
        ) : (
          <ul className="dashboard-list">
            {protocols.map((protocol) => (
              <li key={protocol.protocol_id}>
                <button
                  type="button"
                  disabled={!authoritative}
                  onClick={() => onOpenProtocol(protocol)}
                >
                  <span className="dashboard-list-icon">
                    <FileText aria-hidden="true" />
                  </span>
                  <span className="dashboard-list-body">
                    <strong>{protocol.title}</strong>
                    {protocol.archived_at !== null ? <small>Archived</small> : null}
                    <small>
                      {protocol.latest_version_status === "DRAFT"
                        ? "Draft - review required before it can be run"
                        : "Approved"}
                      {` - ${protocol.step_count} steps`}
                    </small>
                    <small>Imported {relativeTime(protocol.created_at)}</small>
                  </span>
                  <span className="dashboard-list-action">
                    {protocol.latest_version_status === "DRAFT" ? "Review" : "Open"}{" "}
                    <ArrowRight aria-hidden="true" />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {finished.length > 0 ? (
        <section aria-labelledby="finished-title">
          <h2 id="finished-title">Finished sessions</h2>
          <ul className="dashboard-list dashboard-list--muted">
            {finished.map((session) => (
              <li key={session.session_id}>
                <button
                  type="button"
                  disabled={!authoritative}
                  onClick={() => onResumeSession(session)}
                >
                  <span className="dashboard-list-icon">
                    <FlaskConical aria-hidden="true" />
                  </span>
                  <span className="dashboard-list-body">
                    <strong>{session.protocol_title}</strong>
                    {session.label ? <small>Label: {session.label}</small> : null}
                    <small>
                      {stateLabel(session.state)} - {session.completed_step_count} of{" "}
                      {session.step_count} steps recorded
                    </small>
                  </span>
                  <span className="dashboard-list-action">
                    View notebook <ArrowRight aria-hidden="true" />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
