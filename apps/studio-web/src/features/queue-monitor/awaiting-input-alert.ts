import { useEffect, useRef } from "react";

/**
 * Operator alerts for tasks waiting on an answer. The provider gateway gives up on an
 * unanswered question after 30 minutes, so the Queue Monitor surfaces new questions in the
 * tab title and, when the operator opted in, as a browser notification.
 */

export const AWAITING_INPUT_NOTIFICATION_TAG = "musestudio-awaiting-input";

export type NotificationSupport = NotificationPermission | "unsupported";

/** Tab title with an awaiting-input prefix; the base title when nothing is waiting. */
export function awaitingInputTitle(baseTitle: string, count: number): string {
  return count > 0 ? `(${count}) Awaiting input · ${baseTitle}` : baseTitle;
}

/** Notify only when the count grows after a known baseline, never on the first load or a poll. */
export function shouldNotifyAwaitingInput(previous: number | undefined, next: number): boolean {
  return previous !== undefined && next > previous;
}

export function notificationApi(): typeof Notification | undefined {
  if (typeof window === "undefined" || !("Notification" in window)) return undefined;
  return window.Notification;
}

export function readNotificationPermission(): NotificationSupport {
  const api = notificationApi();
  return api ? api.permission : "unsupported";
}

/** Fire a browser notification when permission was granted; otherwise do nothing. */
export function notifyAwaitingInput(count: number): void {
  const api = notificationApi();
  if (!api || api.permission !== "granted") return;
  try {
    const notification = new api("MuseStudio needs your answer", {
      body:
        count === 1
          ? "1 task is waiting for your answer. The provider gives up after 30 minutes."
          : `${count} tasks are waiting for your answer. The provider gives up after 30 minutes.`,
      tag: AWAITING_INPUT_NOTIFICATION_TAG,
    });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers (e.g. Chrome on Android) only allow notifications from a service worker.
  }
}

/**
 * Keep the tab title in sync with the project-wide awaiting count and notify when it grows.
 * `scopeKey` identifies the counted scope (the project); a new scope starts a new baseline.
 * `count` is undefined while no summary is available, which restores the base title.
 * The original title is restored when the monitor unmounts.
 */
export function useAwaitingInputAlert(scopeKey: string, count: number | undefined): void {
  const baseTitle = useRef<string | null>(null);
  const previous = useRef<{ scopeKey: string; count: number } | null>(null);

  useEffect(() => {
    const original = document.title;
    baseTitle.current = original;
    return () => {
      document.title = original;
    };
  }, []);

  useEffect(() => {
    const base = baseTitle.current ?? document.title;
    if (count === undefined) {
      document.title = base;
      return;
    }
    document.title = awaitingInputTitle(base, count);
    const baseline = previous.current?.scopeKey === scopeKey ? previous.current.count : undefined;
    previous.current = { scopeKey, count };
    if (shouldNotifyAwaitingInput(baseline, count)) notifyAwaitingInput(count);
  }, [scopeKey, count]);
}
