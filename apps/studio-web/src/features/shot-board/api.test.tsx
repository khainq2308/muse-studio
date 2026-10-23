import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiPost: vi.fn(),
  apiGetBlob: vi.fn(),
}));

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  apiGet: mocks.apiGet,
  apiPatch: mocks.apiPatch,
  apiPost: mocks.apiPost,
  apiGetBlob: mocks.apiGetBlob,
}));

import {
  useAssetMedia,
  useCompareTakes,
  useEnqueueShotTask,
  useFlagTakeContinuity,
  useLaunchBatch,
  useProjectShots,
  useSelectTake,
  useShotTakes,
  useUpdateShot,
} from "@/features/shot-board/api";
import { buildContinuityFlagRequest } from "@/features/shot-board/takes";
import { buildBatchLaunchRequest, buildEnqueueRequest } from "@/features/shot-board/state";
import {
  makeBatchResult,
  makeComparison,
  makeEnqueuedTask,
  makeFlagResult,
  makeReview,
  makeSelection,
  makeShot,
  makeTake,
} from "@/features/shot-board/test-fixtures";
import { ApiError } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import { useSessionStore } from "@/lib/stores/session-store";

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
  return { queryClient, invalidate, wrapper };
}

let sessionVersion = 0;

beforeEach(() => {
  sessionVersion = useSessionStore.getState().sessionVersion;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Shot Board hooks", () => {
  it("loads the project's shots with the path parameter and abort signal", async () => {
    mocks.apiGet.mockResolvedValue([makeShot()]);
    const { wrapper } = setup();

    const { result } = renderHook(() => useProjectShots("project-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0]?.id).toBe("shot-1");
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/shots",
      expect.objectContaining({
        pathParams: { project_id: "project-1" },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("patches a shot and invalidates the whole project shot list on success and failure", async () => {
    mocks.apiPatch.mockResolvedValueOnce(makeReview());
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useUpdateShot("project-1"), { wrapper });

    act(() => result.current.mutate({ shotId: "shot-1", update: { action: "Runs." } }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPatch).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/shots/{shot_id}",
      { action: "Runs." },
      { pathParams: { project_id: "project-1", shot_id: "shot-1" } },
    );
    expect(result.current.data?.prompt_final).toBe("close-up handheld shot of the hero running");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projectShots(sessionVersion, "project-1"),
    });

    const failure = new ApiError({ status: 422, detail: [{ loc: ["body", "action"], msg: "x" }] });
    mocks.apiPatch.mockRejectedValueOnce(failure);
    invalidate.mockClear();
    act(() => result.current.mutate({ shotId: "shot-1", update: { action: " " } }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBe(failure);
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("enqueues a single shot without an idempotency key and refreshes shots and queue", async () => {
    mocks.apiPost.mockResolvedValue(makeEnqueuedTask({ created_new: false }));
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useEnqueueShotTask("project-1"), { wrapper });
    const request = buildEnqueueRequest({
      shotId: "shot-1",
      connectionId: "conn-mock",
      modelId: "muse-video-mock-v1",
      taskType: "video",
    });

    act(() => result.current.mutate(request));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith("/api/v1/queue/tasks", request);
    const [, body] = mocks.apiPost.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty("idempotency_key");
    expect(body.force_retry).toBe(false);
    expect(result.current.data?.created_new).toBe(false);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projectShots(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.queueTasksPrefix(sessionVersion, "project-1"),
    });
  });

  it("launches a batch for the project path and returns the typed result", async () => {
    mocks.apiPost.mockResolvedValue(makeBatchResult());
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useLaunchBatch("project-1"), { wrapper });
    const request = buildBatchLaunchRequest({
      connectionId: "conn-mock",
      modelId: "muse-video-mock-v1",
      scope: { kind: "scene", sceneId: "scene-1" },
      onlyChanged: true,
    });

    act(() => result.current.mutate(request));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/batch/launch",
      request,
      { pathParams: { project_id: "project-1" } },
    );
    expect(result.current.data?.batch_id).toBe("bat_0001");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.queueTasksPrefix(sessionVersion, "project-1"),
    });
  });
});

describe("Take review hooks", () => {
  it("loads a shot's takes with the shot path parameter and abort signal", async () => {
    mocks.apiGet.mockResolvedValue([makeTake()]);
    const { wrapper } = setup();

    const { result } = renderHook(() => useShotTakes("project-1", "shot-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/api/v1/queue/shots/{shot_id}/takes",
      expect.objectContaining({
        pathParams: { shot_id: "shot-1" },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("compares two takes only once a pair is chosen", async () => {
    const a = makeTake({ id: "take-a" });
    const b = makeTake({ id: "take-b", take_number: 2 });
    mocks.apiGet.mockResolvedValue(makeComparison(a, b));
    const { wrapper } = setup();

    const { result, rerender } = renderHook(
      ({ pair }: { pair: readonly [string, string] | null }) =>
        useCompareTakes("project-1", "shot-1", pair),
      { wrapper, initialProps: { pair: null as readonly [string, string] | null } },
    );
    expect(result.current.fetchStatus).toBe("idle");
    expect(mocks.apiGet).not.toHaveBeenCalled();

    rerender({ pair: ["take-a", "take-b"] });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/shots/{shot_id}/compare-takes",
      expect.objectContaining({
        pathParams: { project_id: "project-1", shot_id: "shot-1" },
        query: { take_a_id: "take-a", take_b_id: "take-b" },
      }),
    );
  });

  it("downloads asset media through the shared client with a size bound", async () => {
    mocks.apiGetBlob.mockResolvedValue(new Blob(["png"], { type: "image/png" }));
    const { wrapper } = setup();

    const { result, rerender } = renderHook(
      ({ assetId }: { assetId: string | null }) => useAssetMedia(assetId, 1024),
      { wrapper, initialProps: { assetId: null as string | null } },
    );
    expect(mocks.apiGetBlob).not.toHaveBeenCalled();

    rerender({ assetId: "asset-1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mocks.apiGetBlob).toHaveBeenCalledWith(
      "/api/v1/assets/{asset_id}/download",
      expect.objectContaining({
        pathParams: { asset_id: "asset-1" },
        maxBytes: 1024,
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("selects a take and invalidates the shot list and every take view, even on failure", async () => {
    mocks.apiPost.mockResolvedValueOnce(
      makeSelection({ selected_take_id: "take-2", propagated_downstream_shots: ["shot-2"] }),
    );
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useSelectTake("project-1"), { wrapper });

    act(() => result.current.mutate({ shotId: "shot-1", takeId: "take-2" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith(
      "/api/v1/queue/shots/{shot_id}/takes/{take_id}/select",
      undefined,
      { pathParams: { shot_id: "shot-1", take_id: "take-2" } },
    );
    expect(result.current.data?.propagated_downstream_shots).toEqual(["shot-2"]);
    for (const queryKey of [
      queryKeys.projectShots(sessionVersion, "project-1"),
      queryKeys.shotTakesPrefix(sessionVersion, "project-1"),
      queryKeys.shotTakeComparePrefix(sessionVersion, "project-1"),
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }

    invalidate.mockClear();
    mocks.apiPost.mockRejectedValueOnce(new ApiError({ status: 404, detail: "Take missing" }));
    act(() => result.current.mutate({ shotId: "shot-1", takeId: "take-9" }));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projectShots(sessionVersion, "project-1"),
    });
  });

  it("posts the continuity flag payload with project, shot and take path parameters", async () => {
    mocks.apiPost.mockResolvedValueOnce(makeFlagResult({ regenerated_task_id: "task-regen" }));
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useFlagTakeContinuity("project-1"), { wrapper });
    const request = buildContinuityFlagRequest({
      take: makeTake(),
      flagged: true,
      directorNotes: "Hair flips.",
      regenerate: { connectionId: "conn-live", modelId: "muse-video-v1" },
    });

    act(() => result.current.mutate({ shotId: "shot-1", takeId: "take-1", request }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/shots/{shot_id}/takes/{take_id}/flag-continuity",
      {
        continuity_flagged: true,
        director_notes: "Hair flips.",
        regenerate_now: true,
        connection_id: "conn-live",
        model_id: "muse-video-v1",
      },
      { pathParams: { project_id: "project-1", shot_id: "shot-1", take_id: "take-1" } },
    );
    expect(result.current.data?.regenerated_task_id).toBe("task-regen");
    for (const queryKey of [
      queryKeys.projectShots(sessionVersion, "project-1"),
      queryKeys.shotTakesPrefix(sessionVersion, "project-1"),
      queryKeys.queueTasksPrefix(sessionVersion, "project-1"),
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
  });
});
