import type { ReactNode } from "react";
import { FileQuestion, RotateCcw } from "lucide-react";

import type { ResourceState } from "../state/useRoutedResource";

interface ResourceGateProps<T> {
  state: ResourceState<T>;
  /** What the route asked for, e.g. "session". Used in the operator-facing copy. */
  kind: string;
  loadingLabel: string;
  onRetry: () => void;
  onGoBack: () => void;
  children: (value: T, id: string) => ReactNode;
}

/**
 * Render a routed resource, or an honest explanation of why it is not there.
 *
 * The gate exists so that "the route asked for something that is not available"
 * has exactly one rendering. Before it, a failed load left the previous
 * resource on screen with live controls, which meant a mis-typed URL could put
 * the operator in front of one experiment while every button acted on another.
 * Here the only three outcomes are: still loading, an explicit not-found or
 * error state, or the matching resource.
 */
export function ResourceGate<T>({
  state,
  kind,
  loadingLabel,
  onRetry,
  onGoBack,
  children,
}: ResourceGateProps<T>) {
  if (state.status === "ready") return <>{children(state.value, state.id)}</>;

  if (state.status === "error") {
    return (
      <main className="not-found-state" id="main-content">
        <FileQuestion aria-hidden="true" />
        <h1>
          {state.notFound
            ? `That ${kind} does not exist`
            : `That ${kind} could not be opened`}
        </h1>
        <p>
          {state.notFound
            ? `LabJarvis has no ${kind} with the identifier in this address. It may have been removed, or the address may be mistyped.`
            : state.error.message}
        </p>
        {state.notFound ? null : state.error.action === null ? null : (
          <p>{state.error.action}</p>
        )}
        <p className="not-found-identifier">
          Requested identifier: <code>{state.id}</code>
        </p>
        <p className="not-found-assurance">
          Nothing was changed. No control on this page acts on any other {kind}.
        </p>
        <div className="not-found-actions">
          <button className="button button--primary" type="button" onClick={onGoBack}>
            Back to the dashboard
          </button>
          {state.notFound ? null : (
            <button className="button button--secondary" type="button" onClick={onRetry}>
              <RotateCcw aria-hidden="true" /> Try again
            </button>
          )}
        </div>
        {state.error.reference === null ? null : (
          <p className="error-reference">Reference: {state.error.reference}</p>
        )}
      </main>
    );
  }

  return (
    <main className="loading-state" id="main-content" aria-busy="true">
      <p>{loadingLabel}</p>
    </main>
  );
}
