import { useState } from "react";
import { Bell, BellOff, BellRing } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  notificationApi,
  readNotificationPermission,
  type NotificationSupport,
} from "@/features/queue-monitor/awaiting-input-alert";

/**
 * Explicit opt-in for browser notifications about new operator questions. Permission is only
 * requested when the operator clicks; nothing is shown when the browser has no Notification API.
 */
export function AwaitingInputNotifications() {
  const [permission, setPermission] = useState<NotificationSupport>(readNotificationPermission);

  if (permission === "unsupported") return null;

  if (permission === "granted") {
    return (
      <p
        data-testid="queue-notifications-status"
        className="flex items-center gap-2 text-xs text-muted-foreground"
      >
        <BellRing className="size-4" aria-hidden="true" />
        Notifications on for new questions
      </p>
    );
  }

  if (permission === "denied") {
    return (
      <p
        data-testid="queue-notifications-status"
        className="flex items-center gap-2 text-xs text-muted-foreground"
      >
        <BellOff className="size-4" aria-hidden="true" />
        Notifications blocked in browser settings
      </p>
    );
  }

  const requestPermission = async () => {
    const api = notificationApi();
    if (!api) return;
    try {
      // Older Safari resolves to undefined and only supports the callback form.
      const result: NotificationPermission | undefined = await api.requestPermission();
      setPermission(result ?? readNotificationPermission());
    } catch {
      setPermission(readNotificationPermission());
    }
  };

  return (
    <Button type="button" variant="outline" size="sm" onClick={() => void requestPermission()}>
      <Bell className="mr-2 size-4" aria-hidden="true" />
      Enable notifications
    </Button>
  );
}
