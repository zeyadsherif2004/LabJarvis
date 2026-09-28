import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  CloudOff,
  FileCheck2,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import type { ClientPort } from "../../api/client";
import type { ActionResult } from "../../actions";
import { errorSentence } from "../../errors";
import type { ProtocolSource, ProtocolStep, ProtocolVersion } from "../../types";
import { ProtocolSourceView } from "./ProtocolSourceView";
import {
  PLACEHOLDER_REQUIREMENT_NAME,
  PLACEHOLDER_STEP_INSTRUCTION,
  PLACEHOLDER_STEP_TITLE,
  approvalBlockers,
  flagLabel,
  unassignedReason,
} from "./reviewChecks";
import { useDraftAutosave, type SaveState } from "./useDraftAutosave";


interface ProtocolReviewViewProps {
  client: ClientPort;
  version: ProtocolVersion;
  busy: boolean;
  onApprove: (
    steps: ProtocolStep[],
    revision: number,
    label: string | null,
  ) => Promise<ActionResult>;
  /** Told when autosave advances the revision, so the parent stays in step. */
  onRevisionChange?: (revision: number) => void;
}

function saveStateMessage(state: SaveState): string {
  switch (state.status) {
    case "clean":
      return "No unsaved changes.";
    case "pending":
      return "Unsaved changes. Saving shortly...";
    case "saving":
      return "Saving your review...";
    case "saved":
      return `Saved as revision ${state.revision}. A reload will bring it back.`;
    case "failed":
      return `Not saved. ${state.message}`;
    case "conflict":
      return state.message;
  }
}

function renumber(steps: ProtocolStep[]): ProtocolStep[] {
  return steps.map((step, index) => ({ ...step, number: index + 1 }));
}

function reviewId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `review-step-${Date.now().toString(36)}`;
}

export function ProtocolReviewView({
  client,
  version,
  busy,
  onApprove,
  onRevisionChange,
}: ProtocolReviewViewProps) {
  const [steps, setSteps] = useState<ProtocolStep[]>(() => structuredClone(version.steps));
  // What the source pane highlights: the focused step's excerpt, or a piece of
  // unused source text the reviewer asked to locate.
  const [highlight, setHighlight] = useState<string | null>(
    () => version.steps[0]?.source.excerpt ?? null,
  );
  const [source, setSource] = useState<ProtocolSource | null>(null);
  const [sourceLoading, setSourceLoading] = useState(true);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [runLabel, setRunLabel] = useState("");

  const autosave = useDraftAutosave({
    client,
    versionId: version.id,
    initialRevision: version.revision,
    enabled: version.status === "DRAFT",
    ...(onRevisionChange === undefined ? {} : { onRevisionChange }),
  });

  // The source pane is loaded per version identity, so moving between drafts
  // can never leave one protocol's document beside another protocol's steps.
  useEffect(() => {
    let current = true;
    setSourceLoading(true);
    setSourceError(null);
    setSource(null);
    client
      .getProtocolSource(version.id)
      .then((loaded) => {
        if (current) setSource(loaded);
      })
      .catch((caught: unknown) => {
        if (current) setSourceError(errorSentence(caught));
      })
      .finally(() => {
        if (current) setSourceLoading(false);
      });
    return () => {
      current = false;
    };
  }, [client, version.id]);

  const edit = useCallback(
    (change: (current: ProtocolStep[]) => ProtocolStep[]): void => setSteps(change),
    [],
  );

  // Autosave is told about an edit once it has actually landed in state.
  // Calling it from inside the updater made it a side effect of rendering,
  // which React is free to run more than once.
  const noteEdit = autosave.noteEdit;
  const untouched = useRef(true);
  useEffect(() => {
    if (untouched.current) {
      untouched.current = false;
      return;
    }
    noteEdit(steps);
  }, [noteEdit, steps]);

  function updateStep(index: number, patch: Partial<ProtocolStep>): void {
    edit((current) =>
      current.map((step, candidate) => (candidate === index ? { ...step, ...patch } : step)),
    );
  }

  function move(index: number, offset: -1 | 1): void {
    edit((current) => {
      const destination = index + offset;
      if (destination < 0 || destination >= current.length) return current;
      const reordered = [...current];
      const selected = reordered[index];
      const displaced = reordered[destination];
      if (selected === undefined || displaced === undefined) return current;
      reordered[index] = displaced;
      reordered[destination] = selected;
      return renumber(reordered);
    });
  }

  function addStep(): void {
    edit((current) => {
      const sourcePage = current.at(-1)?.source.page ?? 1;
      return renumber([
        ...current,
        {
          id: reviewId(),
          number: current.length + 1,
          title: PLACEHOLDER_STEP_TITLE,
          instruction: PLACEHOLDER_STEP_INSTRUCTION,
          requirements: [],
          duration_seconds: null,
          safety_notes: [],
          source: {
            page: sourcePage,
            section: "Operator-added during review",
            excerpt: "Operator-added reviewed step; verify against the source document.",
          },
          parallel_allowed: false,
          flags: [],
        },
      ]);
    });
  }

  function acknowledgeFlag(index: number, flagIndex: number, acknowledged: boolean): void {
    edit((current) =>
      current.map((step, candidate) =>
        candidate === index
          ? {
              ...step,
              flags: (step.flags ?? []).map((flag, position) =>
                position === flagIndex ? { ...flag, acknowledged } : flag,
              ),
            }
          : step,
      ),
    );
  }

  function removeStep(index: number): void {
    edit((current) =>
      current.length === 1 ? current : renumber(current.filter((_, item) => item !== index)),
    );
  }

  const blocked = autosave.state.status === "conflict";
  const blockers = approvalBlockers(steps);
  const openFlagCount = steps.reduce(
    (total, step) => total + (step.flags ?? []).filter((flag) => !flag.acknowledged).length,
    0,
  );
  const unassigned = source?.unassigned_text ?? [];

  return (
    <main className="protocol-review" id="main-content">
      <header className="view-heading">
        <div>
          <h1>Review imported protocol</h1>
          <p>
            Check every instruction against the source document. Parsed content remains a draft until
            you approve it.
          </p>
        </div>
        <span className="revision-label">Draft revision {version.revision}</span>
      </header>

      <div className="review-warning" role="note">
        <ShieldAlert aria-hidden="true" />
        <p>
          LabJarvis proposed these steps from extractable document text. Confirm quantities, timers,
          reagent names, safety notes, order, and source excerpts yourself.
          {openFlagCount > 0
            ? ` ${openFlagCount === 1 ? "1 item was" : `${openFlagCount} items were`} flagged ` +
              "during import because the document did not settle them; each must be checked " +
              "before approval."
            : ""}
        </p>
      </div>

      {/* Save state is stated plainly rather than implied by an icon: an
          operator who steps away needs to know whether their review survived. */}
      <p
        className={`autosave-state autosave-state--${autosave.state.status}`}
        role="status"
      >
        {autosave.state.status === "conflict" ? (
          <CloudOff aria-hidden="true" />
        ) : autosave.state.status === "failed" ? (
          <CloudOff aria-hidden="true" />
        ) : autosave.state.status === "saving" ? (
          <RefreshCw aria-hidden="true" />
        ) : (
          <Save aria-hidden="true" />
        )}
        {saveStateMessage(autosave.state)}
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          // Flush first so approval carries exactly what is on screen and the
          // revision it was saved as, rather than a number that autosave has
          // since moved past.
          void autosave.flush().then((revision) => {
            if (revision === null) return;
            void onApprove(renumber(steps), revision, runLabel.trim() || null);
          });
        }}
      >
        <ol className="review-step-list">
          {steps.map((step, index) => (
            <li
              className="review-step"
              key={step.id}
              onFocusCapture={() => setHighlight(step.source.excerpt)}
            >
              <div className="step-order-controls">
                <span aria-hidden="true">{index + 1}</span>
                <button
                  type="button"
                  aria-label={`Move step ${index + 1} up`}
                  disabled={index === 0 || busy}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label={`Move step ${index + 1} down`}
                  disabled={index === steps.length - 1 || busy}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label={`Remove step ${index + 1}`}
                  disabled={steps.length === 1 || busy}
                  onClick={() => removeStep(index)}
                >
                  <Trash2 aria-hidden="true" />
                </button>
              </div>
              <div className="review-fields">
                {(step.flags ?? []).length > 0 ? (
                  <section
                    className="extraction-flags field-wide"
                    aria-label={`Import flags for step ${index + 1}`}
                  >
                    <h3>
                      <TriangleAlert aria-hidden="true" /> Check before approval
                    </h3>
                    <ul>
                      {(step.flags ?? []).map((flag, flagIndex) => (
                        <li
                          key={`${flag.code}-${flagIndex}`}
                          className={flag.acknowledged ? "extraction-flag--checked" : undefined}
                        >
                          <strong>{flagLabel(flag.code)}</strong>
                          <span>{flag.message}</span>
                          {flag.excerpt ? <q>{flag.excerpt}</q> : null}
                          <label className="extraction-flag-check">
                            <input
                              type="checkbox"
                              checked={flag.acknowledged}
                              disabled={busy}
                              onChange={(event) =>
                                acknowledgeFlag(index, flagIndex, event.target.checked)
                              }
                            />
                            I have checked this against the source
                          </label>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                <label>
                  Step title
                  <input
                    value={step.title}
                    disabled={busy}
                    onChange={(event) => updateStep(index, { title: event.target.value })}
                  />
                </label>
                <label className="field-wide">
                  Safety notes, one per line
                  <textarea
                    value={step.safety_notes.join("\n")}
                    rows={2}
                    disabled={busy}
                    onChange={(event) =>
                      updateStep(index, {
                        safety_notes: event.target.value
                          .split("\n")
                          .map((note) => note.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </label>
                <label className="field-wide">
                  Reviewed instruction
                  <textarea
                    value={step.instruction}
                    rows={3}
                    disabled={busy}
                    onChange={(event) => updateStep(index, { instruction: event.target.value })}
                  />
                </label>
                <label>
                  Timer in seconds
                  <input
                    type="number"
                    min="1"
                    value={step.duration_seconds ?? ""}
                    disabled={busy}
                    onChange={(event) =>
                      updateStep(index, {
                        duration_seconds:
                          event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                  />
                </label>
                <div className="source-reference">
                  <FileCheck2 aria-hidden="true" />
                  <span>
                    Page {step.source.page}
                    {step.source.section ? ` - ${step.source.section}` : ""}
                  </span>
                  <small>{step.source.excerpt}</small>
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={() => setHighlight(step.source.excerpt)}
                  >
                    Find this in the source document
                  </button>
                </div>
                {step.requirements.map((requirement, requirementIndex) => (
                  <fieldset className="requirement-fields" key={`${step.id}-${requirementIndex}`}>
                    <legend>Required reagent or material</legend>
                    <label>
                      Name
                      <input
                        value={requirement.name}
                        disabled={busy}
                        onChange={(event) => {
                          const requirements = step.requirements.map((item, candidate) =>
                            candidate === requirementIndex
                              ? { ...item, name: event.target.value }
                              : item,
                          );
                          updateStep(index, { requirements });
                        }}
                      />
                    </label>
                    <label>
                      Quantity
                      <input
                        value={requirement.quantity ?? ""}
                        inputMode="decimal"
                        disabled={busy}
                        onChange={(event) => {
                          const requirements = step.requirements.map((item, candidate) =>
                            candidate === requirementIndex
                              ? { ...item, quantity: event.target.value || null }
                              : item,
                          );
                          updateStep(index, { requirements });
                        }}
                      />
                    </label>
                    <label>
                      Unit
                      <input
                        value={requirement.unit ?? ""}
                        disabled={busy}
                        onChange={(event) => {
                          const requirements = step.requirements.map((item, candidate) =>
                            candidate === requirementIndex
                              ? { ...item, unit: event.target.value || null }
                              : item,
                          );
                          updateStep(index, { requirements });
                        }}
                      />
                    </label>
                    <label>
                      Aliases, comma separated
                      <input
                        value={requirement.aliases.join(", ")}
                        disabled={busy}
                        onChange={(event) => {
                          const requirements = step.requirements.map((item, candidate) =>
                            candidate === requirementIndex
                              ? {
                                  ...item,
                                  aliases: event.target.value
                                    .split(",")
                                    .map((alias) => alias.trim())
                                    .filter(Boolean),
                                }
                              : item,
                          );
                          updateStep(index, { requirements });
                        }}
                      />
                    </label>
                    <button
                      className="button button--secondary"
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        updateStep(index, {
                          requirements: step.requirements.filter(
                            (_, candidate) => candidate !== requirementIndex,
                          ),
                        })
                      }
                    >
                      <Trash2 aria-hidden="true" /> Remove requirement
                    </button>
                  </fieldset>
                ))}
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    updateStep(index, {
                      requirements: [
                        ...step.requirements,
                        {
                          // Positional within the step, matching how extraction
                          // assigns them, so the id stays unique after review.
                          requirement_id: `${step.id}-req-${step.requirements.length + 1}`,
                          name: PLACEHOLDER_REQUIREMENT_NAME,
                          quantity: null,
                          unit: null,
                          aliases: [],
                        },
                      ],
                    })
                  }
                >
                  <Plus aria-hidden="true" /> Add requirement
                </button>
              </div>
            </li>
          ))}
        </ol>
        <button
          className="button button--secondary"
          type="button"
          disabled={busy}
          onClick={addStep}
        >
          <Plus aria-hidden="true" /> Add reviewed step
        </button>
        <label className="field-wide">
          Run label (optional)
          <input
            value={runLabel}
            maxLength={120}
            disabled={busy}
            placeholder="For example: batch 7, replicate A"
            onChange={(event) => setRunLabel(event.target.value)}
          />
          <small>Do not enter participant, patient, or personal identifiers.</small>
        </label>
        <div className="review-footer">
          <p>
            <CheckCircle2 aria-hidden="true" /> Approval freezes this version for session use.
          </p>
          <button
            className="button button--primary"
            type="submit"
            disabled={busy || blocked || blockers.length > 0}
            aria-describedby={blockers.length > 0 ? "approval-blockers" : undefined}
          >
            {busy ? "Approving..." : "Approve reviewed protocol"}
          </button>
        </div>
        {blockers.length > 0 ? (
          <div className="approval-blockers" id="approval-blockers" role="status">
            <p>Before this protocol can be approved:</p>
            <ul>
              {blockers.map((blocker, position) => (
                <li key={`${blocker.stepNumber}-${position}`}>
                  Step {blocker.stepNumber}: {blocker.text}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </form>

      {unassigned.length > 0 ? (
        <section className="unassigned-source" aria-labelledby="unassigned-source-title">
          <h2 id="unassigned-source-title">Source text not used as steps</h2>
          <p>
            Import kept this text but did not place it in any step. Check that nothing here is an
            instruction the protocol needs; if it is, add it to a step above.
          </p>
          <ul>
            {unassigned.map((item, position) => (
              <li key={`${item.page}-${position}`}>
                <div className="unassigned-source-meta">
                  <strong>{unassignedReason(item.reason)}</strong>
                  <span>
                    Page {item.page}
                    {item.section ? ` - ${item.section}` : ""}
                  </span>
                </div>
                <pre>{item.text}</pre>
                <button
                  className="button button--secondary"
                  type="button"
                  onClick={() => setHighlight(item.text)}
                >
                  Find this in the source document
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ProtocolSourceView
        source={source}
        loading={sourceLoading}
        error={sourceError}
        highlightExcerpt={highlight}
      />
    </main>
  );
}
