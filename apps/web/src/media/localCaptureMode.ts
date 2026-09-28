/** Browser preference for local label checks; live scanning stays the default. */
export type LocalCaptureMode = "live" | "timed_single";

const STORAGE_KEY = "labjarvis.localCaptureMode";

export function loadLocalCaptureMode(): LocalCaptureMode {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === "timed_single"
      ? "timed_single"
      : "live";
  } catch {
    return "live";
  }
}

export function saveLocalCaptureMode(mode: LocalCaptureMode): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, mode);
  } catch {
    // A blocked storage API only limits this preference to the current page.
  }
}
