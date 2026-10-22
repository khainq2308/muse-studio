import { describe, expect, it } from "vitest";
import {
  getQueuePollIntervalMs,
  getQueueRefetchInterval,
  QUEUE_POLL_WINDOW_MS,
} from "@/features/queue-monitor/state";

describe("queue monitor polling policy", () => {
  it("backs off from two to five to ten seconds", () => {
    expect(getQueuePollIntervalMs(0)).toBe(2_000);
    expect(getQueuePollIntervalMs(59_999)).toBe(2_000);
    expect(getQueuePollIntervalMs(60_000)).toBe(5_000);
    expect(getQueuePollIntervalMs(5 * 60_000)).toBe(10_000);
  });

  it("stops the polling window after thirty minutes", () => {
    expect(getQueuePollIntervalMs(QUEUE_POLL_WINDOW_MS)).toBe(false);
    expect(getQueueRefetchInterval(["running"], QUEUE_POLL_WINDOW_MS)).toBe(false);
  });

  it("polls while a task is queued or running", () => {
    expect(getQueueRefetchInterval(["queued", "paused"], 10_000)).toBe(2_000);
    expect(getQueueRefetchInterval(["succeeded", "running"], 90_000)).toBe(5_000);
  });

  it("stops when all tasks are paused or terminal", () => {
    expect(getQueueRefetchInterval(["paused"], 1_000)).toBe(false);
    expect(
      getQueueRefetchInterval(["succeeded", "failed", "poll_timeout", "canceled"], 1_000),
    ).toBe(false);
    expect(getQueueRefetchInterval([], 1_000)).toBe(false);
  });

  it("polls a pending initial request but stops polling after a query error", () => {
    expect(getQueueRefetchInterval(undefined, 15_000, "pending")).toBe(2_000);
    expect(getQueueRefetchInterval(undefined, QUEUE_POLL_WINDOW_MS + 1, "pending")).toBe(false);
    expect(getQueueRefetchInterval(undefined, 15_000, "error")).toBe(false);
    expect(getQueueRefetchInterval(["running"], 15_000, "error")).toBe(false);
  });

  it("continues polling active tasks even when they are on another page", () => {
    expect(getQueueRefetchInterval(["succeeded"], 15_000, "success", 4)).toBe(2_000);
    expect(getQueueRefetchInterval(["running"], 15_000, "success", 0)).toBe(false);
  });
});
