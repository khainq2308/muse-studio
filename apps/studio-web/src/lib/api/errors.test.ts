import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { authErrorMessage, isAuthError, shouldRetryApiRequest } from "@/lib/api/errors";

describe("shared API error classification", () => {
  it("classifies unauthorized and forbidden responses as auth-related", () => {
    expect(isAuthError(new ApiError({ status: 401, detail: "Unauthorized" }))).toBe(true);
    expect(isAuthError(new ApiError({ status: 403, detail: "Forbidden" }))).toBe(true);
    expect(isAuthError(new ApiError({ status: 500, detail: "Internal error" }))).toBe(false);
    expect(isAuthError(new Error("network error"))).toBe(false);
  });

  it("asks for a token when a 401 occurs without an active token", () => {
    const error = new ApiError({ status: 401, detail: "Unauthorized" });
    expect(authErrorMessage(error, "", "projects")).toBe("Admin token required to read projects.");
  });

  it("distinguishes a rejected token from resource-level permission denial", () => {
    const unauthorized = new ApiError({ status: 401, detail: "Unauthorized" });
    const forbidden = new ApiError({ status: 403, detail: "Forbidden" });

    expect(authErrorMessage(unauthorized, "configured-token", "projects")).toContain("rejected");
    expect(authErrorMessage(forbidden, "configured-token", "projects")).toContain(
      "does not have permission",
    );
    expect(authErrorMessage(forbidden, "configured-token", "projects")).not.toContain("rejected");
  });

  it("provides a generic retry message for non-auth errors", () => {
    expect(authErrorMessage(new Error("network"), "", "projects")).toBe(
      "Could not load projects. Check the Orchestrator and retry.",
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
});
