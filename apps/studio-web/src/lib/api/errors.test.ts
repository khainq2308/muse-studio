import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import {
  actionErrorMessage,
  apiRetryDelay,
  authErrorMessage,
  isAuthError,
  shouldRetryApiRequest,
} from "@/lib/api/errors";

describe("shared API error classification", () => {
  it("classifies unauthorized and forbidden responses as auth-related", () => {
    expect(isAuthError(new ApiError({ status: 401, detail: "Unauthorized" }))).toBe(true);
    expect(isAuthError(new ApiError({ status: 403, detail: "Forbidden" }))).toBe(true);
    expect(isAuthError(new ApiError({ status: 500, detail: "Internal error" }))).toBe(false);
    expect(isAuthError(new Error("network error"))).toBe(false);
  });

  it("asks for a token when a 401 occurs without an active token", () => {
    const error = new ApiError({ status: 401, detail: "Unauthorized" });
    expect(authErrorMessage(error, "", "projects")).toBe("Cần có token quản trị để đọc projects.");
  });

  it("distinguishes a rejected token from resource-level permission denial", () => {
    const unauthorized = new ApiError({ status: 401, detail: "Unauthorized" });
    const forbidden = new ApiError({ status: 403, detail: "Forbidden" });

    expect(authErrorMessage(unauthorized, "configured-token", "projects")).toContain("bị từ chối");
    expect(authErrorMessage(forbidden, "configured-token", "projects")).toContain("không có quyền");
    expect(authErrorMessage(forbidden, "configured-token", "projects")).not.toContain("bị từ chối");
  });

  it("provides a generic retry message for non-auth errors", () => {
    expect(authErrorMessage(new Error("network"), "", "projects")).toBe(
      "Không thể tải projects. Kiểm tra Orchestrator và thử lại.",
    );
  });

  it("does not retry permanent HTTP statuses", () => {
    const permanentStatuses = [400, 401, 403, 404, 422];

    for (const status of permanentStatuses) {
      expect(shouldRetryApiRequest(0, new ApiError({ status, detail: null }))).toBe(false);
    }
  });

  it("retries transient errors at most once", () => {
    const error = new Error("network failure");

    expect(shouldRetryApiRequest(0, error)).toBe(true);
    expect(shouldRetryApiRequest(1, error)).toBe(false);
  });

  it("honors explicit API retryability metadata", () => {
    const nonRetryableError = new ApiError({
      status: 500,
      detail: "internal",
      retryable: false,
    });
    const retryableError = new ApiError({
      status: 500,
      detail: "internal",
      retryable: true,
    });

    expect(shouldRetryApiRequest(0, nonRetryableError)).toBe(false);
    expect(shouldRetryApiRequest(0, retryableError)).toBe(true);
  });

  it("does not retry 500 or unknown 5xx without an envelope retryable flag", () => {
    expect(shouldRetryApiRequest(0, new ApiError({ status: 500, detail: null }))).toBe(false);
    expect(shouldRetryApiRequest(0, new ApiError({ status: 501, detail: null }))).toBe(false);
    for (const status of [502, 503, 504]) {
      expect(shouldRetryApiRequest(0, new ApiError({ status, detail: null }))).toBe(true);
      expect(shouldRetryApiRequest(1, new ApiError({ status, detail: null }))).toBe(false);
    }
  });

  it("retries 429 and honors Retry-After within a bound", () => {
    const limited = new ApiError({ status: 429, detail: null, retryAfterMs: 4_000 });
    expect(shouldRetryApiRequest(0, limited)).toBe(true);
    expect(apiRetryDelay(0, limited)).toBe(4_000);
    expect(
      apiRetryDelay(0, new ApiError({ status: 429, detail: null, retryAfterMs: 999_999 })),
    ).toBe(30_000);
    expect(apiRetryDelay(0, new Error("network"))).toBe(1_000);
    expect(apiRetryDelay(1, new Error("network"))).toBe(2_000);
  });

  it("surfaces validation detail for failed write actions", () => {
    const invalid = new ApiError({
      status: 422,
      detail: [
        { loc: ["body", "max_concurrency"], msg: "Input should be less than or equal to 20" },
      ],
    });
    expect(actionErrorMessage(invalid, "token", "create connection")).toContain(
      "less than or equal to 20",
    );
    const timeout = new ApiError({ status: 0, detail: "timed out", category: "timeout" });
    expect(actionErrorMessage(timeout, "token", "create backup")).toContain(
      "Hãy làm mới trước khi thử lại",
    );
    const unauthorized = new ApiError({ status: 401, detail: "Unauthorized" });
    expect(actionErrorMessage(unauthorized, "", "delete backup")).toBe(
      "Cần có token quản trị để delete backup.",
    );
  });
});
