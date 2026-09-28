import { useCallback, useEffect, useRef, useState } from "react";

import { ApiProblem, type ClientPort } from "../../api/client";
import { errorSentence } from "../../errors";
import type { ProtocolStep } from "../../types";

/**
 * Keep an in-progress protocol review on the server.
 *
 * Losing an hour of careful review to a reload is not a rare edge case; it is
 * what happens the first time a laboratory machine goes to sleep. Two design
 * choices follow from that:
 *
 * - **The draft is saved to the database, not to browser storage.** The server
 *   is already the authority for protocol revisions, and saving there means the
 *   recovered draft is the same object on any browser, survives a cleared
 *   cache, and is covered by the same version checks as everything else.
 * - **The version identifier is captured once and asserted on every save.** The
 *   editor is remounted when the route names a different version, but a
 *   half-finished autosave from the previous draft must never be able to land
 *   on the new one, so the save refuses if the identifier no longer matches.
 */

export type SaveState =
  | { status: "clean" }
  | { status: "pending" }
  | { status: "saving" }
  | { status: "saved"; at: number; revision: number }
  | { status: "failed"; message: string }
  | { status: "conflict"; message: string };

export interface DraftAutosave {
  state: SaveState;
  revision: number;
  /** Record an edit; the save itself is debounced. */
  noteEdit: (steps: ProtocolStep[]) => void;
  /** Save immediately, e.g. before approving. Resolves to the saved revision. */
  flush: () => Promise<number | null>;
}

const DEBOUNCE_MS = 1500;

export function useDraftAutosave(options: {
  client: ClientPort;
  versionId: string;
  initialRevision: number;
  /** Autosave is only meaningful while the version is still a draft. */
  enabled: boolean;
  onRevisionChange?: (revision: number) => void;
  debounceMs?: number;
}): DraftAutosave {
  const {
    client,
    versionId,
    initialRevision,
    enabled,
    onRevisionChange,
    debounceMs = DEBOUNCE_MS,
  } = options;

  const [state, setState] = useState<SaveState>({ status: "clean" });
  const [revision, setRevision] = useState(initialRevision);

  const revisionRef = useRef(initialRevision);
  revisionRef.current = revision;
  const versionRef = useRef(versionId);
  versionRef.current = versionId;
  const pendingSteps = useRef<ProtocolStep[] | null>(null);
  const timer = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);
  const inFlight = useRef<Promise<number | null> | null>(null);
  const notifyRef = useRef(onRevisionChange);
  notifyRef.current = onRevisionChange;

  const save = useCallback(async (): Promise<number | null> => {
    const steps = pendingSteps.current;
    if (steps === null) return revisionRef.current;
    const targetVersion = versionRef.current;
    if (targetVersion !== versionId) {
      // The editor moved to another draft while this save was queued. Writing
      // one protocol's steps onto another is the one outcome that would be
      // worse than losing the edit.
      return null;
    }
    pendingSteps.current = null;
    setState({ status: "saving" });
    try {
      const saved = await client.reviewProtocol(versionId, steps, revisionRef.current);
      revisionRef.current = saved.revision;
      setRevision(saved.revision);
      notifyRef.current?.(saved.revision);
      setState({ status: "saved", at: Date.now(), revision: saved.revision });
      return saved.revision;
    } catch (caught) {
      const conflict =
        caught instanceof ApiProblem &&
        (caught.problem.code === "PROTOCOL_VERSION_CONFLICT" ||
          caught.problem.code === "PROTOCOL_NOT_EDITABLE");
      setState(
        conflict
          ? {
              status: "conflict",
              message:
                "This draft was changed somewhere else, so your recent edits were not " +
                "saved. Reload the draft before continuing.",
            }
          : { status: "failed", message: errorSentence(caught) },
      );
      return null;
    }
  }, [client, versionId]);

  const noteEdit = useCallback(
    (steps: ProtocolStep[]) => {
      if (!enabled) return;
      pendingSteps.current = steps;
      setState((current) =>
        current.status === "conflict" ? current : { status: "pending" },
      );
      if (timer.current !== null) globalThis.clearTimeout(timer.current);
      timer.current = globalThis.setTimeout(() => {
        inFlight.current = save();
      }, debounceMs);
    },
    [debounceMs, enabled, save],
  );

  const flush = useCallback(async (): Promise<number | null> => {
    if (timer.current !== null) {
      globalThis.clearTimeout(timer.current);
      timer.current = null;
    }
    if (inFlight.current !== null) await inFlight.current;
    if (pendingSteps.current === null) return revisionRef.current;
    return save();
  }, [save]);

  useEffect(
    () => () => {
      if (timer.current !== null) globalThis.clearTimeout(timer.current);
    },
    [],
  );

  return { state, revision, noteEdit, flush };
}
