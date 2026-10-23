import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  awaitingInputTitle,
  shouldNotifyAwaitingInput,
  useAwaitingInputAlert,
} from "@/features/queue-monitor/awaiting-input-alert";
import { AwaitingInputNotifications } from "@/features/queue-monitor/components/awaiting-input-notifications";

class FakeNotification {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(async () => FakeNotification.permission);
  static created: FakeNotification[] = [];
  onclick: (() => void) | null = null;
  close = vi.fn();
  constructor(
    public title: string,
    public options?: NotificationOptions,
  ) {
    FakeNotification.created.push(this);
  }
}

const originalTitle = "MuseStudio";

beforeEach(() => {
  document.title = originalTitle;
  FakeNotification.permission = "granted";
  FakeNotification.created = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal("Notification", FakeNotification);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("awaitingInputTitle", () => {
  it("prefixes the count and leaves the base title when nothing waits", () => {
    expect(awaitingInputTitle("MuseStudio", 2)).toBe("(2) Awaiting input · MuseStudio");
    expect(awaitingInputTitle("MuseStudio", 0)).toBe("MuseStudio");
  });
});

describe("shouldNotifyAwaitingInput", () => {
  it("notifies only when the count grows after a known baseline", () => {
    expect(shouldNotifyAwaitingInput(undefined, 3)).toBe(false);
    expect(shouldNotifyAwaitingInput(1, 1)).toBe(false);
    expect(shouldNotifyAwaitingInput(2, 1)).toBe(false);
    expect(shouldNotifyAwaitingInput(1, 2)).toBe(true);
  });
});

describe("useAwaitingInputAlert", () => {
  it("updates the title and notifies on increases only, then restores the title", () => {
    const { rerender, unmount } = renderHook(
      ({ scope, count }: { scope: string; count: number | undefined }) =>
        useAwaitingInputAlert(scope, count),
      { initialProps: { scope: "project-1", count: 1 as number | undefined } },
    );
    // First load sets the baseline without notifying.
    expect(document.title).toBe("(1) Awaiting input · MuseStudio");
    expect(FakeNotification.created).toHaveLength(0);

    rerender({ scope: "project-1", count: 1 });
    expect(FakeNotification.created).toHaveLength(0);

    rerender({ scope: "project-1", count: 2 });
    expect(document.title).toBe("(2) Awaiting input · MuseStudio");
    expect(FakeNotification.created).toHaveLength(1);
    expect(FakeNotification.created[0].options?.body).toContain("2 tasks are waiting");

    rerender({ scope: "project-1", count: 0 });
    expect(document.title).toBe("MuseStudio");

    // A new project starts a new baseline.
    rerender({ scope: "project-2", count: 3 });
    expect(FakeNotification.created).toHaveLength(1);

    unmount();
    expect(document.title).toBe(originalTitle);
  });

  it("does not notify without permission", () => {
    FakeNotification.permission = "default";
    const { rerender } = renderHook(
      ({ count }: { count: number }) => useAwaitingInputAlert("p", count),
      {
        initialProps: { count: 0 },
      },
    );
    rerender({ count: 1 });
    expect(document.title).toBe("(1) Awaiting input · MuseStudio");
    expect(FakeNotification.created).toHaveLength(0);
  });

  it("focuses the window when the notification is clicked", () => {
    const focus = vi.spyOn(window, "focus").mockImplementation(() => undefined);
    const { rerender } = renderHook(
      ({ count }: { count: number }) => useAwaitingInputAlert("p", count),
      {
        initialProps: { count: 0 },
      },
    );
    rerender({ count: 1 });
    FakeNotification.created[0].onclick?.();
    expect(focus).toHaveBeenCalled();
    expect(FakeNotification.created[0].close).toHaveBeenCalled();
  });
});

describe("AwaitingInputNotifications", () => {
  it("asks for permission only when the operator clicks", async () => {
    FakeNotification.permission = "default";
    render(<AwaitingInputNotifications />);
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();

    FakeNotification.requestPermission.mockImplementationOnce(async () => {
      FakeNotification.permission = "granted";
      return "granted";
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Enable notifications" }));
    });
    expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("queue-notifications-status")).toHaveTextContent(
      "Notifications on for new questions",
    );
  });

  it("explains a blocked permission and renders nothing without the API", () => {
    FakeNotification.permission = "denied";
    const { unmount } = render(<AwaitingInputNotifications />);
    expect(screen.getByTestId("queue-notifications-status")).toHaveTextContent("blocked");
    unmount();

    vi.unstubAllGlobals();
    // jsdom has no Notification API of its own.
    const descriptor = Object.getOwnPropertyDescriptor(window, "Notification");
    if (descriptor) Reflect.deleteProperty(window, "Notification");
    const { container } = render(<AwaitingInputNotifications />);
    expect(container).toBeEmptyDOMElement();
    if (descriptor) Object.defineProperty(window, "Notification", descriptor);
  });
});
