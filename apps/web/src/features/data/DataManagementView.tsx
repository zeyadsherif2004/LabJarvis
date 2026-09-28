import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
import { ArchiveRestore, DatabaseBackup, Download, ShieldAlert, Trash2 } from "lucide-react";

import type { ClientPort } from "../../api/client";
import { errorSentence } from "../../errors";
import type {
  BackupPackage,
  DeletionReceipt,
  ReadinessResponse,
  RestoreValidation,
} from "../../types";
import "./data-management.css";

interface DataManagementViewProps {
  client: ClientPort;
  readiness: ReadinessResponse;
  sessionId: string | null;
  onSessionDeleted: (receipt: DeletionReceipt) => void;
  onProtocolDeleted?: (receipt: DeletionReceipt) => void;
}

interface TypedDeleteDialogProps {
  objectType: "session" | "protocol";
  objectId: string;
  opener: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
  onDelete: (confirmation: string) => Promise<void>;
}

function TypedDeleteDialog({
  objectType,
  objectId,
  opener,
  onClose,
  onDelete,
}: TypedDeleteDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const expected = `DELETE ${objectId}`;
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    return () => opener.current?.focus();
  }, [opener]);

  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>): void {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const first = inputRef.current;
    const last = cancelRef.current;
    if (first === null || last === null) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function confirmDelete(): Promise<void> {
    if (confirmation !== expected || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onDelete(confirmation);
    } catch (caught) {
      setError(errorSentence(caught));
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  return (
    <div className="typed-dialog-backdrop">
      <div
        className="typed-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
      >
        <h2 id={titleId}>Permanently delete {objectType}</h2>
        <p id={descriptionId}>
          This removes the complete {objectType} aggregate and its retained media. It cannot
          be undone. Export or back up anything you need first.
        </p>
        <label htmlFor={`${titleId}-confirmation`}>Type {expected} to confirm</label>
        <input
          id={`${titleId}-confirmation`}
          ref={inputRef}
          value={confirmation}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setConfirmation(event.target.value)}
        />
        {error !== null ? <p role="alert">{error}</p> : null}
        <div className="typed-dialog-actions">
          <button
            className="button button--danger"
            type="button"
            disabled={confirmation !== expected || busy}
            onClick={() => void confirmDelete()}
          >
            {busy ? "Deleting..." : `Permanently delete ${objectType}`}
          </button>
          <button
            ref={cancelRef}
            className="button button--secondary"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

export function DataManagementView({
  client,
  readiness,
  sessionId,
  onSessionDeleted,
  onProtocolDeleted,
}: DataManagementViewProps) {
  const [sessionTarget, setSessionTarget] = useState(sessionId ?? "");
  const [protocolTarget, setProtocolTarget] = useState("");
  const [dialog, setDialog] = useState<"session" | "protocol" | null>(null);
  const sessionDeleteRef = useRef<HTMLButtonElement>(null);
  const protocolDeleteRef = useRef<HTMLButtonElement>(null);
  const [backup, setBackup] = useState<BackupPackage | null>(null);
  const [backupName, setBackupName] = useState("");
  const [validation, setValidation] = useState<RestoreValidation | null>(null);
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupError, setBackupError] = useState<string | null>(null);

  useEffect(() => {
    if (sessionId !== null) setSessionTarget(sessionId);
  }, [sessionId]);

  async function createBackup(): Promise<void> {
    setBackupBusy(true);
    setBackupError(null);
    setValidation(null);
    try {
      const created = await client.createBackup();
      setBackup(created);
      setBackupName(created.backup_name);
    } catch (caught) {
      setBackupError(errorSentence(caught));
    } finally {
      setBackupBusy(false);
    }
  }

  async function validateBackup(): Promise<void> {
    const normalized = backupName.trim();
    if (!normalized) return;
    setBackupBusy(true);
    setBackupError(null);
    try {
      setValidation(await client.validateBackup(normalized));
    } catch (caught) {
      setBackupError(errorSentence(caught));
    } finally {
      setBackupBusy(false);
    }
  }

  const normalizedSession = sessionTarget.trim();
  const normalizedProtocol = protocolTarget.trim();
  const providerBoundary = readiness.privacy.external_model_processing
    ? `Model inputs may leave this machine for ${readiness.hosted_provider ?? "the configured hosted provider"}.`
    : "Model processing stays on this machine in the current profile.";

  return (
    <main className="data-management" id="main-content">
      <header className="view-heading">
        <div>
          <p className="eyebrow">Operator-controlled lifecycle</p>
          <h1>Data management</h1>
          <p>Export, back up, archive, stage restores, or explicitly delete local records.</p>
        </div>
      </header>

      <section className="data-card" aria-labelledby="export-session-title">
        <Download aria-hidden="true" />
        <div>
          <h2 id="export-session-title">Export session data</h2>
          <p>Export the append-only notebook before considering deletion.</p>
          {sessionId === null ? (
            <label>
              Session ID
              <input
                value={sessionTarget}
                onChange={(event) => setSessionTarget(event.target.value)}
              />
            </label>
          ) : (
            <p><strong>Session:</strong> {sessionId}</p>
          )}
          {normalizedSession ? (
            <div className="data-actions">
              <a className="button button--secondary" href={client.exportNotebook(normalizedSession, "json")}>Export JSON</a>
              <a className="button button--secondary" href={client.exportNotebook(normalizedSession, "csv")}>Export CSV</a>
            </div>
          ) : (
            <p>Enter a session ID to enable exports.</p>
          )}
        </div>
      </section>

      <section className="data-card" aria-labelledby="delete-session-title">
        <Trash2 aria-hidden="true" />
        <div>
          <h2 id="delete-session-title">Delete session</h2>
          <p>Deletes the session aggregate, notebook, audit trail, and associated retained media.</p>
          <button
            ref={sessionDeleteRef}
            className="button button--danger"
            type="button"
            disabled={!normalizedSession}
            onClick={() => setDialog("session")}
          >
            Delete session
          </button>
        </div>
      </section>

      <section className="data-card" aria-labelledby="delete-protocol-title">
        <ShieldAlert aria-hidden="true" />
        <div>
          <h2 id="delete-protocol-title">Delete unused protocol</h2>
          <p>
            Referenced protocols cannot be deleted. Archive them instead. Only unused drafts or
            archived, unreferenced protocols are eligible.
          </p>
          <label>
            Protocol ID
            <input
              value={protocolTarget}
              onChange={(event) => setProtocolTarget(event.target.value)}
            />
          </label>
          <button
            ref={protocolDeleteRef}
            className="button button--danger"
            type="button"
            disabled={!normalizedProtocol}
            onClick={() => setDialog("protocol")}
          >
            Delete protocol
          </button>
        </div>
      </section>

      <section className="data-card" aria-labelledby="backup-title">
        <DatabaseBackup aria-hidden="true" />
        <div>
          <h2 id="backup-title">Backup and staged restore</h2>
          <p>
            Backups are created online with a SHA-256 manifest. Validation copies a backup into
            an inactive staging area; it never replaces the active database.
          </p>
          <button
            className="button button--primary"
            type="button"
            disabled={backupBusy}
            onClick={() => void createBackup()}
          >
            Create verified backup
          </button>
          {backup !== null ? (
            <p role="status">
              Created <strong>{backup.backup_name}</strong> ({backup.manifest.files.length} verified files,
              schema {backup.manifest.schema_revision}).
            </p>
          ) : null}
          <label>
            Backup name
            <input value={backupName} onChange={(event) => setBackupName(event.target.value)} />
          </label>
          <button
            className="button button--secondary"
            type="button"
            disabled={backupBusy || !backupName.trim()}
            onClick={() => void validateBackup()}
          >
            <ArchiveRestore aria-hidden="true" /> Validate and stage restore
          </button>
          {validation !== null ? (
            <p role="status">
              Verified and staged as <strong>{validation.staging_name}</strong>. The staged restore
              is not activated automatically; activation requires an explicit offline operator step.
            </p>
          ) : null}
          {backupError !== null ? <p role="alert">{backupError}</p> : null}
        </div>
      </section>

      <section className="data-card data-card--boundary" aria-labelledby="retention-title">
        <DatabaseBackup aria-hidden="true" />
        <div>
          <h2 id="retention-title">Retention and provider boundary</h2>
          <ul>
            <li>{readiness.privacy.raw_media_retention ? "Raw media is retained." : "Raw media is not retained after processing unless it belongs to an explicit record."}</li>
            <li>{readiness.privacy.transcript_retention ? "Transcripts are retained in the session record." : "Transcripts are not retained."}</li>
            <li>{providerBoundary}</li>
          </ul>
        </div>
      </section>

      {dialog === "session" && normalizedSession ? (
        <TypedDeleteDialog
          objectType="session"
          objectId={normalizedSession}
          opener={sessionDeleteRef}
          onClose={() => setDialog(null)}
          onDelete={async (confirmation) => {
            const receipt = await client.deleteSession(normalizedSession, confirmation);
            setDialog(null);
            onSessionDeleted(receipt);
          }}
        />
      ) : null}
      {dialog === "protocol" && normalizedProtocol ? (
        <TypedDeleteDialog
          objectType="protocol"
          objectId={normalizedProtocol}
          opener={protocolDeleteRef}
          onClose={() => setDialog(null)}
          onDelete={async (confirmation) => {
            const receipt = await client.deleteProtocol(normalizedProtocol, confirmation);
            setDialog(null);
            onProtocolDeleted?.(receipt);
          }}
        />
      ) : null}
    </main>
  );
}
