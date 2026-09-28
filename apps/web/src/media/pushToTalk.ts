import { useEffect, useRef } from "react";

/**
 * Keys offered for push-to-talk. USB foot pedals ship emulating a keyboard key;
 * most can be set to one of these, and F-keys never collide with typing.
 */
export const PUSH_TO_TALK_KEYS = ["F8", "F9", "F10", "F12", "Pause", "ScrollLock", "PageDown"] as const;
export type PushToTalkKey = (typeof PUSH_TO_TALK_KEYS)[number];
export const DEFAULT_PUSH_TO_TALK_KEY: PushToTalkKey = "F8";
const STORAGE_KEY = "labjarvis.pushToTalkKey";

export function loadPushToTalkKey(): PushToTalkKey {
  try {
    const stored = globalThis.localStorage?.getItem(STORAGE_KEY);
    return PUSH_TO_TALK_KEYS.find((key) => key === stored) ?? DEFAULT_PUSH_TO_TALK_KEY;
  } catch {
    return DEFAULT_PUSH_TO_TALK_KEY;
  }
}

export function savePushToTalkKey(key: PushToTalkKey): void {
  try {
    globalThis.localStorage?.setItem(STORAGE_KEY, key);
  } catch {
    // Storage can be blocked; the key then lasts for this page only.
  }
}

/** True when keyboard input is going into a field and must not start capture. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (target as HTMLInputElement).type;
    return !["button", "checkbox", "radio", "submit", "reset", "range", "color", "file"].includes(
      type,
    );
  }
  return false;
}

interface PushToTalkOptions {
  key: string;
  enabled: boolean;
  onPress: () => void;
  onRelease: () => void;
  /** Escape while held discards the recording instead of submitting it. */
  onCancel: () => void;
}

/**
 * Document-level hold-to-talk: works wherever focus is, except in text fields.
 *
 * Release is honoured even if focus moved or the window lost focus while the
 * key was held, so a stuck recording cannot outlive the pedal press.
 */
export function usePushToTalkKey({ key, enabled, onPress, onRelease, onCancel }: PushToTalkOptions) {
  const held = useRef(false);
  const handlers = useRef({ onPress, onRelease, onCancel });
  handlers.current = { onPress, onRelease, onCancel };

  useEffect(() => {
    if (!enabled) return undefined;
    const matches = (event: KeyboardEvent) => event.key === key || event.code === key;

    function down(event: KeyboardEvent) {
      if (held.current && event.key === "Escape") {
        event.preventDefault();
        held.current = false;
        handlers.current.onCancel();
        return;
      }
      if (!matches(event) || event.repeat || held.current) return;
      if (isEditableTarget(event.target)) return;
      event.preventDefault();
      held.current = true;
      handlers.current.onPress();
    }
    function up(event: KeyboardEvent) {
      if (!matches(event) || !held.current) return;
      event.preventDefault();
      held.current = false;
      handlers.current.onRelease();
    }
    function blur() {
      if (!held.current) return;
      held.current = false;
      handlers.current.onRelease();
    }
    document.addEventListener("keydown", down);
    document.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      document.removeEventListener("keydown", down);
      document.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
      held.current = false;
    };
  }, [enabled, key]);
}
