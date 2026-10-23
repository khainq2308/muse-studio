import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiRequest: vi.fn(),
  apiGetBlob: vi.fn(),
  downloadFile: vi.fn(),
}));

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  apiGet: mocks.apiGet,
  apiPost: mocks.apiPost,
  apiRequest: mocks.apiRequest,
  apiGetBlob: mocks.apiGetBlob,
  downloadFile: mocks.downloadFile,
}));

import {
  useExportProjectShots,
  useImportScript,
  useReferenceAssetMedia,
  useStoryBible,
  useUploadReferenceAsset,
  useUpsertStoryBibleEntry,
} from "@/features/script-studio/api";
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

describe("Script Studio hooks", () => {
  it("loads the project's Story Bible entries with the project path parameter", async () => {
    mocks.apiGet.mockResolvedValue([
      {
        id: "bib_1",
        entry_type: "character",
        code_name: "char_linh",
        display_name: "Detective Linh",
        immutable_traits: "sharp eyes",
        mutable_attributes: { current_outfit: "trench coat" },
        reference_asset_ids: [],
        requires_reference_lock: false,
      },
    ]);
    const { wrapper } = setup();

    const { result } = renderHook(() => useStoryBible("project-1"), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0]?.code_name).toBe("char_linh");
    expect(mocks.apiGet).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/story-bible",
      expect.objectContaining({
        pathParams: { project_id: "project-1" },
        signal: expect.any(AbortSignal),
      }),
    );
  });

  it("upserts a Story Bible entry and invalidates both storyBible and projectShots", async () => {
    mocks.apiPost.mockResolvedValue({
      id: "bib_1",
      code_name: "char_linh",
      status: "recompiled_downstream_shots",
    });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useUpsertStoryBibleEntry("project-1"), { wrapper });

    const request = {
      entry_type: "character",
      code_name: "char_linh",
      display_name: "Detective Linh",
      immutable_traits: "sharp silver eyes",
      mutable_attributes: { current_outfit: "trench coat" },
      reference_asset_ids: ["ast_1"],
      requires_reference_lock: true,
    };

    act(() => result.current.mutate(request));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/story-bible",
      request,
      { pathParams: { project_id: "project-1" } },
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.storyBible(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projectShots(sessionVersion, "project-1"),
    });
  });

  it("imports a script and invalidates Story Bible, shots, takes, queue, and projects", async () => {
    mocks.apiPost.mockResolvedValue({
      script_revision_id: "scr_001",
      version_number: 2,
      raw_content_preserved: true,
      diff_summary: "Detected 1 scene and 2 shots.",
      detected_characters: 1,
      detected_locations: 1,
      detected_scenes: 1,
      generated_shots: 2,
      warnings: [],
    });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useImportScript("project-1"), { wrapper });

    const request = {
      raw_content: "SCENE 1: Alley\nSHOT 1: Arrival",
      source_format: "museflow",
      auto_apply_proposal: true,
      replace_existing: false,
    };

    act(() => result.current.mutate(request));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiPost).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/scripts/import",
      request,
      { pathParams: { project_id: "project-1" } },
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.storyBible(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projectShots(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.shotTakesPrefix(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.shotTakeComparePrefix(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.queueTasksPrefix(sessionVersion, "project-1"),
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projects(sessionVersion),
    });
  });

  it("uploads a reference asset via FormData and invalidates the project list", async () => {
    mocks.apiRequest.mockResolvedValue({
      id: "ast_ref99",
      project_id: "project-1",
      asset_type: "reference",
      filename: "linh.png",
      mime_type: "image/png",
      size_bytes: 1024,
      sha256: "abc",
      is_locked_reference: true,
      created_at: "2026-01-01T00:00:00Z",
    });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useUploadReferenceAsset("project-1"), { wrapper });

    const file = new File([new Uint8Array([137, 80, 78, 71])], "linh.png", { type: "image/png" });
    act(() => result.current.mutate({ file, isLockedReference: true }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(mocks.apiRequest).toHaveBeenCalledWith(
      "/api/v1/assets/upload",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
      }),
    );
    const [, options] = mocks.apiRequest.mock.calls[0] as [string, { body: FormData }];
    expect(options.body.get("project_id")).toBe("project-1");
    expect(options.body.get("asset_type")).toBe("reference");
    expect(options.body.get("is_locked_reference")).toBe("true");
    expect(options.body.get("file")).toBe(file);
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: queryKeys.projects(sessionVersion),
    });
  });

  it("downloads reference asset bytes and exports project shots as JSON/CSV", async () => {
    const blob = new Blob(["png"], { type: "image/png" });
    mocks.apiGetBlob.mockResolvedValue(blob);
    mocks.downloadFile.mockResolvedValue(undefined);
    const { wrapper } = setup();

    const mediaHook = renderHook(() => useReferenceAssetMedia("ast_ref1", 1024), { wrapper });
    await waitFor(() => expect(mediaHook.result.current.isSuccess).toBe(true));
    expect(mocks.apiGetBlob).toHaveBeenCalledWith(
      "/api/v1/assets/{asset_id}/download",
      expect.objectContaining({
        maxBytes: 1024,
        pathParams: { asset_id: "ast_ref1" },
      }),
    );

    const exportHook = renderHook(() => useExportProjectShots("project-1"), { wrapper });
    act(() => exportHook.result.current.mutate("csv"));
    await waitFor(() => expect(exportHook.result.current.isSuccess).toBe(true));
    expect(mocks.downloadFile).toHaveBeenCalledWith(
      "/api/v1/projects/{project_id}/shots/export",
      "project-1-shots.csv",
      {
        pathParams: { project_id: "project-1" },
        query: { format: "csv" },
      },
    );
  });
});
