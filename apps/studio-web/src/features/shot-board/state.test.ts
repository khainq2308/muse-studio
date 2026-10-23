import { describe, expect, it } from "vitest";
import {
  buildBatchLaunchRequest,
  buildEnqueueRequest,
  buildShotUpdate,
  connectionModelOptions,
  generationErrorMessage,
  groupShotsByScene,
  mapShotUpdateError,
  modelOptionsForTaskType,
  previousShotCandidates,
  shotApprovalLabel,
  shotEditDefaults,
  shotGenerationLabel,
  type ShotApprovalState,
  type ShotGenerationState,
} from "@/features/shot-board/state";
import { makeConnection, makeShot } from "@/features/shot-board/test-fixtures";
import { ApiError } from "@/lib/api/client";

describe("groupShotsByScene", () => {
  it("groups by scene in first-appearance order and sorts shots by sequence index", () => {
    const groups = groupShotsByScene([
      makeShot({ id: "b2", scene_id: "scene-b", sequence_index: 4 }),
      makeShot({ id: "a1", scene_id: "scene-a", sequence_index: 1 }),
      makeShot({ id: "b1", scene_id: "scene-b", sequence_index: 3 }),
      makeShot({ id: "a2", scene_id: "scene-a", sequence_index: 2 }),
    ]);

    expect(groups.map((group) => group.sceneId)).toEqual(["scene-a", "scene-b"]);
    expect(groups[0]?.shots.map((shot) => shot.id)).toEqual(["a1", "a2"]);
    expect(groups[1]?.shots.map((shot) => shot.id)).toEqual(["b1", "b2"]);
  });
});

describe("buildShotUpdate", () => {
  it("returns an empty patch when nothing changed", () => {
    const shot = makeShot();
    expect(buildShotUpdate(shot, shotEditDefaults(shot))).toEqual({});
  });

  it("sends only changed fields, trims text and maps an empty previous shot to null", () => {
    const shot = makeShot({ previous_shot_id: "shot-0", continuity_mode: "frame_chain" });
    const update = buildShotUpdate(shot, {
      ...shotEditDefaults(shot),
      action: "  The hero runs.  ",
      duration_seconds: 6.5,
      previous_shot_id: "",
      continuity_mode: "standard",
      approval_state: "needs_review",
    });

    expect(update).toEqual({
      action: "The hero runs.",
      duration_seconds: 6.5,
      previous_shot_id: null,
      continuity_mode: "standard",
      approval_state: "needs_review",
    });
  });

  it("offers only other shots of the same scene as previous shots", () => {
    const shot = makeShot({ id: "s2", scene_id: "scene-a", sequence_index: 2 });
    const candidates = previousShotCandidates(shot, [
      shot,
      makeShot({ id: "s1", scene_id: "scene-a", sequence_index: 1 }),
      makeShot({ id: "x1", scene_id: "scene-b", sequence_index: 1 }),
    ]);
    expect(candidates.map((candidate) => candidate.id)).toEqual(["s1"]);
  });
});

describe("mapShotUpdateError", () => {
  it("maps request-validation items to inline field errors", () => {
    const mapping = mapShotUpdateError(
      new ApiError({
        status: 422,
        category: "validation",
        retryable: false,
        detail: [
          { type: "greater_than", loc: ["body", "duration_seconds"], msg: "Input should be > 0" },
          { type: "value_error", loc: ["body", "action"], msg: "Value error, must not be blank" },
        ],
      }),
    );

    expect(mapping.fieldErrors).toEqual({
      duration_seconds: "Input should be > 0",
      action: "Value error, must not be blank",
    });
    expect(mapping.formError).toBeNull();
  });

  it("keeps validation items for fields the form does not show at form level", () => {
    const mapping = mapShotUpdateError(
      new ApiError({
        status: 422,
        detail: [{ loc: ["body", "reference_asset_ids"], msg: "Too many items" }],
      }),
    );
    expect(mapping.fieldErrors).toEqual({});
    expect(mapping.formError).toBe("The shot was not saved: reference_asset_ids: Too many items");
  });

  it("attaches plain-text approval and predecessor errors to their fields", () => {
    const approval = mapShotUpdateError(
      new ApiError({
        status: 422,
        detail: "Không thể Approve Shot khi còn lỗi Blocker: MISSING_REF - Reference missing",
      }),
    );
    expect(approval.fieldErrors.approval_state).toContain("Blocker");
    expect(approval.formError).toBeNull();

    const conflict = mapShotUpdateError(
      new ApiError({ status: 409, detail: "Shot 'a' đã có shot kế tiếp 'b'; previous_shot_id" }),
    );
    expect(conflict.fieldErrors.previous_shot_id).toBeDefined();
  });

  it("reports auth, missing-shot and unknown failures at form level", () => {
    expect(mapShotUpdateError(new ApiError({ status: 401, detail: "no" })).formError).toMatch(
      /admin token was rejected/,
    );
    expect(mapShotUpdateError(new ApiError({ status: 403, detail: "no" })).formError).toMatch(
      /does not have permission/,
    );
    expect(mapShotUpdateError(new ApiError({ status: 404, detail: "gone" })).formError).toMatch(
      /no longer exists/,
    );
    expect(mapShotUpdateError(new ApiError({ status: 500, detail: "boom" })).formError).toBe(
      "The shot was not saved: boom",
    );
    expect(mapShotUpdateError(new TypeError("offline")).formError).toBe(
      "Could not save the shot: offline",
    );
  });
});

describe("generation requests", () => {
  it("builds an enqueue payload without an idempotency key or forced retry", () => {
    const request = buildEnqueueRequest({
      shotId: "shot-1",
      connectionId: "conn-1",
      modelId: "  muse-image-v1 ",
      taskType: "image",
    });

    expect(request).toEqual({
      shot_id: "shot-1",
      connection_id: "conn-1",
      model_id: "muse-image-v1",
      task_type: "image",
      priority: 10,
      max_polls: 60,
      force_retry: false,
    });
    expect(request).not.toHaveProperty("idempotency_key");
  });

  it("builds batch payloads for the whole project or a single scene", () => {
    expect(
      buildBatchLaunchRequest({
        connectionId: "conn-1",
        modelId: "m",
        scope: { kind: "project" },
        onlyChanged: true,
      }),
    ).toEqual({ connection_id: "conn-1", model_id: "m", only_changed: true });

    expect(
      buildBatchLaunchRequest({
        connectionId: "conn-1",
        modelId: "m",
        scope: { kind: "scene", sceneId: "scene-2" },
        onlyChanged: false,
      }),
    ).toEqual({ connection_id: "conn-1", model_id: "m", only_changed: false, scene_id: "scene-2" });
  });

  it("explains a persisted batch whose tasks could not be scheduled", () => {
    const message = generationErrorMessage(
      new ApiError({
        status: 503,
        detail: {
          code: "queue_dispatch_unavailable",
          batch_id: "bat_9",
          task_ids: ["t1"],
          message: "Batch and tasks are saved; do not relaunch the batch.",
        },
      }),
      "batch",
    );
    expect(message).toContain("bat_9");
    expect(message).toContain("do not relaunch");
    expect(generationErrorMessage(new ApiError({ status: 401, detail: "x" }), "enqueue")).toMatch(
      /^The shot was not queued: the admin token was rejected/,
    );
  });
});

describe("connection models", () => {
  it("reads discovered models defensively from the untyped capability snapshot", () => {
    const connection = makeConnection({
      capabilities_snapshot: {
        models: [
          { model_id: "video-a", modality: "video", verification_status: "verified" },
          { model_id: "", modality: "video" },
          "not-a-model",
          { model_id: "any-b" },
        ],
      },
    });

    expect(connectionModelOptions(connection).map((model) => model.modelId)).toEqual([
      "video-a",
      "any-b",
    ]);
    expect(modelOptionsForTaskType(connection, "image").map((model) => model.modelId)).toEqual([
      "any-b",
    ]);
    expect(connectionModelOptions(makeConnection({ capabilities_snapshot: {} }))).toEqual([]);
  });
});

describe("state labels", () => {
  it("labels unknown states from a newer backend instead of rendering blanks", () => {
    expect(shotApprovalLabel("approved")).toBe("Approved");
    expect(shotApprovalLabel("archived" as ShotApprovalState)).toBe("Unknown approval (archived)");
    expect(shotGenerationLabel("blocked_upstream_frame")).toBe("Blocked: upstream frame missing");
    expect(shotGenerationLabel("exploded" as ShotGenerationState)).toBe("Unknown state (exploded)");
  });
});
