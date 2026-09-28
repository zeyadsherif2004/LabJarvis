import { useCallback, useEffect, useRef, useState } from "react";

import { ApiProblem } from "../api/client";
import { describeError, type DisplayableError } from "../errors";

/**
 * A resource whose identity comes from the URL.
 *
 * The point of carrying the id inside every state is that "what is on screen"
 * and "what the URL asked for" can be compared directly. Previously the app
 * held a bare `snapshot | null`: when a route pointed at a session that did not
 * exist, the fetch failed, the old snapshot stayed in memory, and the previous
 * experiment was rendered - with working controls - underneath a URL naming a
 * different resource. Clicking one of those controls mutated the *old* session.
 *
 * Keeping the id in the state makes that class of bug unrepresentable: the
 * caller reads {@link RoutedResource.value}, which is `null` unless the loaded
 * id is the id the route asked for.
 */
export type ResourceState<T> =
  | { status: "idle" }
  | { status: "loading"; id: string }
  | { status: "ready"; id: string; value: T }
  | { status: "error"; id: string; notFound: boolean; error: DisplayableError };

export interface RoutedResource<T> {
  state: ResourceState<T>;
  /** The loaded value, but only when it belongs to the currently routed id. */
  value: T | null;
  /** True while a different resource is being fetched. Mutations must be off. */
  loading: boolean;
  reload: () => Promise<T | null>;
  /** Replace the value in place. Ignored when the id no longer matches. */
  patch: (updater: (current: T) => T) => void;
  /** Seed a value the caller already has, so navigating to it does not refetch. */
  seed: (id: string, value: T) => void;
}

function toState<T>(id: string, caught: unknown): ResourceState<T> {
  return {
    status: "error",
    id,
    notFound: caught instanceof ApiProblem && caught.problem.status === 404,
    error: describeError(caught),
  };
}

/**
 * Load one resource for the active route, discarding anything that arrives late.
 *
 * Two ordering hazards are handled here rather than by each caller:
 *
 * - **Route changes.** The state moves to `loading` for the *new* id before the
 *   request starts, so nothing belonging to the previous id can render.
 * - **Out-of-order responses.** Each request takes a token; a response whose
 *   token is not the newest is dropped. A slow response for session A can
 *   therefore never overwrite a fast response for session B.
 */
export function useRoutedResource<T>(
  id: string | null,
  load: (id: string) => Promise<T>,
): RoutedResource<T> {
  const [state, setState] = useState<ResourceState<T>>({ status: "idle" });
  const token = useRef(0);
  // The effect must not re-run merely because `state` changed, but it does need
  // to know whether the value it would fetch is already present.
  const latest = useRef(state);
  latest.current = state;

  const fetchInto = useCallback(
    async (target: string, options: { keepCurrent?: boolean } = {}): Promise<T | null> => {
      const mine = ++token.current;
      if (options.keepCurrent !== true) {
        setState({ status: "loading", id: target });
      }
      try {
        const value = await load(target);
        if (token.current !== mine) return null;
        setState({ status: "ready", id: target, value });
        return value;
      } catch (caught) {
        if (token.current !== mine) return null;
        // A refresh that fails still changes what is true: if the session has
        // gone, continuing to show it with live controls is the original bug in
        // a different disguise.
        setState(toState<T>(target, caught));
        return null;
      }
    },
    [load],
  );

  useEffect(() => {
    if (id === null) {
      // Leaving the route drops the resource. Holding it "just in case" is what
      // let a stale session survive into an unrelated screen.
      token.current += 1;
      setState({ status: "idle" });
      return;
    }
    const current = latest.current;
    if (current.status === "ready" && current.id === id) return;
    if (current.status === "loading" && current.id === id) return;
    if (current.status === "error" && current.id === id) return;
    void fetchInto(id);
  }, [fetchInto, id]);

  /**
   * Re-read the resource in place.
   *
   * Deliberately does *not* pass through a loading state when the same resource
   * is already on screen. Blanking it first tore the session view down after
   * every single action - which, besides flickering, discarded transient state
   * the view legitimately owned, such as a reagent reading the operator had
   * just confirmed.
   */
  const reload = useCallback(async (): Promise<T | null> => {
    if (id === null) return null;
    const current = latest.current;
    const loaded = current.status === "ready" && current.id === id;
    return fetchInto(id, { keepCurrent: loaded });
  }, [fetchInto, id]);

  const patch = useCallback(
    (updater: (current: T) => T): void => {
      setState((current) =>
        current.status === "ready" && current.id === id
          ? { ...current, value: updater(current.value) }
          : current,
      );
    },
    [id],
  );

  const seed = useCallback((seededId: string, value: T): void => {
    token.current += 1;
    setState({ status: "ready", id: seededId, value });
  }, []);

  const matches = state.status === "ready" && state.id === id;
  return {
    state,
    value: matches ? (state as { value: T }).value : null,
    loading: id !== null && !matches && state.status !== "error",
    reload,
    patch,
    seed,
  };
}
