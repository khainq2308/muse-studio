import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueueMonitorPage } from "@/features/queue-monitor/pages/queue-monitor-page";
import type { QueueTask, QueueTaskPage } from "@/features/queue-monitor/state";
import { DEFAULT_REQUEST_TIMEOUT_MS } from "@/lib/api/client";
import { apiRetryDelay, shouldRetryApiRequest } from "@/lib/api/errors";

// Exercises the real query hook, polling policy and API client (only fetch is stubbed) to
// guard the hung-request and failed-refresh regressions from the deep review.

function makeTask(id: string): QueueTask {
  return {
    account_used: null,
    attempt_count: 1,
    connection_id: "connection-1",
    error_category: null,
    error_message_redacted: null,
    external_task_id: null,
    history_log: [],
    id,
    idempotency_key: id,
    max_attempts: 3,
    max_polls: 60,
    model_id: "muse-video-mock-v1",
    next_poll_delay_seconds: 2,
    poll_count: 0,
    priority: 1,
    project_id: "project-1",
    result_asset_id: null,
    retryable: true,
    scene_id: null,
    shot_id: "shot-1",
    state: "running",
    take_id: null,
    task_type: "video",
  };
}

const page: QueueTaskPage = {
  items: [makeTask("task-a")],
  total: 1,
  limit: 50,
  offset: 0,
  has_more: false,
  summary: { total_tasks: 1, active_tasks: 1, failed_tasks: 0 },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function mount() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { staleTime: 15_000, retry: shouldRetryApiRequest, retryDelay: apiRetryDelay },
    },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <QueueMonitorPage projectId="project-1" />
    </QueryClientProvider>,
  );
  return queryClient;
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("QueueMonitorPage with the real query client", () => {
  it("times out a hung refresh, keeps Refresh usable and keeps the loaded tasks", async () => {
    let calls = 0;
    const fetchMock = vi.fn<typeof fetch>(() =>
      ++calls === 1 ? Promise.resolve(json(page)) : new Promise<Response>(() => undefined),
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = mount();
    await advance(0);
    expect(screen.getByTestId("queue-task-task-a")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await advance(100);
    // A hung request no longer disables Refresh; clicking again cancels and restarts it.
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await advance(100);
    const callsAfterSecondClick = fetchMock.mock.calls.length;
    expect(callsAfterSecondClick).toBeGreaterThanOrEqual(3);
    expect(fetchMock.mock.calls[1]?.[1]?.signal?.aborted).toBe(true);

    // Timeout, one bounded retry, then a second timeout surface a retryable error.
    await advance(DEFAULT_REQUEST_TIMEOUT_MS);
    await advance(apiRetryDelay(1, null));
    await advance(DEFAULT_REQUEST_TIMEOUT_MS);

    expect(screen.getByRole("alert")).toHaveTextContent(/timed out/);
    expect(screen.getByTestId("queue-task-task-a")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    queryClient.clear();
  });

  it("keeps polling at the slowest interval after a retryable refresh failure", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        ++calls === 1 ? json(page) : json({ detail: "Upstream unavailable", retryable: true }, 503),
      ),
    );
    const queryClient = mount();
    await advance(0);
    expect(screen.getByTestId("queue-task-task-a")).toBeTruthy();

    // First poll at 2s fails (503), one retry 1s later fails too -> error state with data.
    await advance(2_000);
    await advance(1_000);
    expect(screen.getByRole("alert")).toHaveTextContent(/showing the last loaded results/);
    expect(screen.getByTestId("queue-task-task-a")).toBeTruthy();
    const callsAtError = calls;

    // Normal backoff would poll again after 2s; the retryable error slows it to 10s.
    await advance(9_900);
    expect(calls).toBe(callsAtError);
    await advance(200);
    expect(calls).toBeGreaterThan(callsAtError);
    queryClient.clear();
  });

  it("stops polling after a non-retryable refresh failure", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () =>
        ++calls === 1 ? json(page) : json({ detail: "Unauthorized" }, 401),
      ),
    );
    const queryClient = mount();
    await advance(0);
    await advance(2_000);
    await advance(50);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByTestId("queue-task-task-a")).toBeTruthy();
    const callsAtError = calls;

    await advance(60_000);
    expect(calls).toBe(callsAtError);
    queryClient.clear();
  });
});
