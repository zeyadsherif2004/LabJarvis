import { ApiProblem } from "./api/client";

export type ActionFailure = { ok: false; code?: string };
type ActionSuccess<T> = T extends undefined ? { ok: true } : { ok: true; value: T };
export type ActionResult<T = undefined> = ActionSuccess<T> | ActionFailure;

export const successfulAction: ActionResult = { ok: true };

export function successfulResult<T>(value: T): ActionResult<T> {
  return { ok: true, value } as ActionSuccess<T>;
}

export function failedAction(code?: string): ActionFailure {
  return code === undefined ? { ok: false } : { ok: false, code };
}

export function actionFailure(caught: unknown): ActionFailure {
  return failedAction(caught instanceof ApiProblem ? caught.problem.code : undefined);
}
