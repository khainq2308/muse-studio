import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  DEFAULT_REQUEST_TIMEOUT_MS,
  LONG_REQUEST_TIMEOUT_MS,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiRequest,
  apiPut,
  apiGetText,
  apiGetBlob,
  downloadFile,
  encodePathSegment,
  normalizeApiBaseUrl,
  parseRetryAfter,
} from "@/lib/api/client";
import { useSessionStore } from "@/lib/stores/session-store";

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  useSessionStore.getState().clearAdminToken();
});

describe("API base URL normalization", () => {
  it.each(["", ".", "/"])("treats %j as same-origin", (value) => {
    expect(normalizeApiBaseUrl(value)).toBe("");
  });

  it("accepts an absolute HTTP(S) URL", () => {
    expect(normalizeApiBaseUrl("https://orchestrator.example.test/")).toBe(
      "https://orchestrator.example.test",
    );
  });

  it("rejects protocol-relative, non-HTTP, and path-prefixed URLs", () => {
    expect(() => normalizeApiBaseUrl("//evil.test")).toThrow();
    expect(() => normalizeApiBaseUrl("file:///etc/passwd")).toThrow();
    expect(() => normalizeApiBaseUrl("https://orchestrator.example.test/api/")).toThrow(
      "origin only",
    );
  });

  it("uses VITE_ORCHESTRATOR_URL when constructing request URLs", async () => {
    vi.stubEnv("VITE_ORCHESTRATOR_URL", "https://orchestrator.example.test/");
    vi.resetModules();

    const { apiGet: apiGetWithConfiguredBase } = await import("@/lib/api/client");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    await apiGetWithConfiguredBase("/api/v1/projects");

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "https://orchestrator.example.test/api/v1/projects",
    );
  });
});

describe("API client", () => {
  it("uses absolute API paths and attaches the admin token without persisting it", async () => {
    useSessionStore.getState().setAdminToken("test-admin-token");
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify([{ id: "project-1" }]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/api/v1/queue/tasks", {
      query: { project_id: "project-1", state: "running", account_used: null },
    });

    const [requestedUrl, init] = fetchMock.mock.calls[0]!;
    expect(String(requestedUrl)).toContain(
      "/api/v1/queue/tasks?project_id=project-1&state=running",
    );
    expect(new Headers(init?.headers).get("X-Studio-Admin-Token")).toBe("test-admin-token");
    expect(window.localStorage.length).toBe(0);
  });

  it("propagates the caller's AbortSignal to fetch and rejects as an abort", async () => {
    const controller = new AbortController();
    let fetchSignal: AbortSignal | undefined;
    const fetchMock = vi.fn<typeof fetch>((_url, init) => {
      fetchSignal = init?.signal ?? undefined;
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetchMock);

    const request = apiGet("/api/v1/projects", { signal: controller.signal });
    expect(fetchSignal?.aborted).toBe(false);
    controller.abort();

    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    await expect(request).rejects.not.toBeInstanceOf(ApiError);
    expect(fetchSignal?.aborted).toBe(true);
  });

  it("times out a request that never resolves with a retryable timeout ApiError", async () => {
    vi.useFakeTimers();
    let fetchSignal: AbortSignal | undefined;
    // Simulates a hung upstream that also ignores AbortSignal.
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((_url, init) => {
        fetchSignal = init?.signal ?? undefined;
        return new Promise<Response>(() => undefined);
      }),
    );

    const request = apiGet("/api/v1/projects");
    const settled = vi.fn();
    request.then(settled, settled);

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS - 1);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);

    const error = await request.catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, category: "timeout", retryable: true });
    expect(fetchSignal?.aborted).toBe(true);
  });

  it("applies the timeout to a stalled response body and honors a per-call override", async () => {
    vi.useFakeTimers();
    const stalledBody = {
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: () => new Promise(() => undefined),
    } as unknown as Response;
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(stalledBody));

    const request = apiGet("/api/v1/projects", { timeoutMs: 1_000 });
    const outcome = request.catch((reason: unknown) => reason);
    await vi.advanceTimersByTimeAsync(1_000);

    await expect(outcome).resolves.toMatchObject({ category: "timeout", retryable: true });
  });

  it("gives multipart uploads the long timeout by default", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() => new Promise<Response>(() => undefined)),
    );
    const form = new FormData();
    form.append("file", new Blob(["zip"]), "backup.zip");

    const request = apiRequest("/api/v1/backups/restore", { method: "POST", body: form });
    const settled = vi.fn();
    request.then(settled, settled);

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS * 2);
    expect(settled).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS);
    await expect(request).rejects.toMatchObject({ category: "timeout" });
  });

  it("sends the expected HTTP methods and JSON bodies", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ id: "created" }))
      .mockResolvedValueOnce(jsonResponse({ id: "replaced" }))
      .mockResolvedValueOnce(jsonResponse({ id: "patched" }))
      .mockResolvedValueOnce(jsonResponse({ id: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);

    await apiPost("/api/v1/queue/tasks", {
      shot_id: "shot-1",
      connection_id: "connection-1",
      task_type: "video",
      model_id: "muse-video-mock-v1",
      priority: 10,
      idempotency_key: "client-test-1",
      force_retry: false,
      max_polls: 60,
    });
    await apiPut("/api/v1/projects/project-1", { name: "Replaced" });
    await apiPatch("/api/v1/projects/project-1", { name: "Patched" });
    await apiDelete("/api/v1/projects/project-1");

    expect(fetchMock.mock.calls.map(([, init]) => init?.method)).toEqual([
      "POST",
      "PUT",
      "PATCH",
      "DELETE",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      shot_id: "shot-1",
      connection_id: "connection-1",
      task_type: "video",
      model_id: "muse-video-mock-v1",
      priority: 10,
      idempotency_key: "client-test-1",
      force_retry: false,
      max_polls: 60,
    });
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Content-Type")).toBe(
      "application/json",
    );
  });

  it("parses JSON content types case-insensitively", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "content-type": "Application/JSON; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/healthz")).resolves.toEqual({ status: "ok" });
  });

  it("uses a fallback for whitespace-only validation messages", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: [{ loc: ["body", "name"], msg: "   " }] }), {
        status: 422,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as Error).message).toBe("API request failed (422)");
  });

  it("reads explicit text API responses without weakening the JSON client", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("# exported markdown", {
        status: 200,
        headers: { "content-type": "text/markdown; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGetText("/api/v1/projects/project-1/shots/export")).resolves.toBe(
      "# exported markdown",
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Accept")).toBe(
      "text/plain, text/markdown",
    );
  });

  it("rejects non-text responses from apiGetText", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGetText("/api/v1/projects")).rejects.toMatchObject({
      status: 200,
      category: "invalid_response",
      retryable: false,
    });
  });

  it("preserves structured validation details in ApiError", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: [{ loc: ["body", "name"], msg: "Field required" }],
          category: "validation",
          retryable: false,
        }),
        {
          status: 422,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      detail: [{ loc: ["body", "name"], msg: "Field required" }],
      category: "validation",
      retryable: false,
    });
  });

  it("formats an object detail message without rendering [object Object]", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ detail: { message: "Access denied", code: "denied" } }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as Error).message).toBe("Access denied");
  });

  it("substitutes encoded path parameters from the generated OpenAPI template", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/api/v1/projects/{project_id}/shots", {
      pathParams: { project_id: "project 42" },
    });

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/projects/project%2042/shots");
  });

  it("rejects missing or unexpected path parameters before fetching", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/api/v1/projects/{project_id}/shots")).rejects.toThrow(
      "API path parameters are required",
    );
    await expect(
      apiRequest("/api/v1/projects/{project_id}/shots", {
        pathParams: { project_id: "project-1", unexpected: "value" },
      }),
    ).rejects.toThrow("Unexpected API path parameter(s): unexpected.");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("encodes API path parameters as one safe segment", () => {
    expect(encodePathSegment("project-42 with space")).toBe("project-42%20with%20space");
    expect(() => encodePathSegment("..")).toThrow("path separators or dot segments");
    expect(() => encodePathSegment("project/..")).toThrow("path separators or dot segments");
    expect(() => encodePathSegment("project\\..")).toThrow("path separators or dot segments");
  });

  it.each([
    "/api/v1/projects/../connections",
    "/api/v1/projects/%2e%2e/connections",
    "/api/v1/projects/a%2F..",
    "/api/v1//projects",
  ])("rejects traversal or repeated-separator path %s before fetching", async (path) => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest(path)).rejects.toThrow(
      /dot segments|path separators|repeated separators/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects malformed JSON on successful responses as a non-retryable ApiError", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("{", {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 200,
      category: "invalid_response",
      retryable: false,
    });
    expect((error as Error).message).toBe("Invalid JSON API response (200).");
  });

  it("classifies a bodyless 201 JSON response as a non-retryable ApiError", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("", {
        status: 201,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 201,
      category: "invalid_response",
      retryable: false,
    });
    expect((error as Error).message).toBe("Invalid JSON API response (201).");
  });

  it("rejects HTML returned with a successful API status", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("<html><body>proxy page</body></html>", {
        status: 200,
        headers: { "content-type": "text/html; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/v1/projects")).rejects.toThrow("Expected JSON API response (200).");
  });

  it("captures Retry-After on rate-limited responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(JSON.stringify({ detail: "slow down" }), {
          status: 429,
          headers: { "content-type": "application/json", "retry-after": "3" },
        }),
      ),
    );
    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);
    expect(error).toMatchObject({ status: 429, retryAfterMs: 3_000 });
    expect(
      parseRetryAfter("Thu, 22 Oct 2026 00:00:05 GMT", Date.parse("2026-10-22T00:00:00Z")),
    ).toBe(5_000);
    expect(parseRetryAfter("garbage")).toBeUndefined();
  });

  it("uses a generic error for non-JSON upstream error pages", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        "<html><body><h1>502 Bad Gateway</h1><pre>internal proxy details</pre></body></html>",
        {
          status: 502,
          headers: { "content-type": "text/html; charset=utf-8" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, detail: null });
    expect((error as Error).message).toBe("API request failed (502)");
    expect((error as Error).message).not.toContain("internal proxy details");
  });

  it("releases the blob URL when the browser download click throws", async () => {
    const createObjectUrl = vi.fn<(object: Blob | MediaSource) => string>(
      () => "blob:test-download-failure",
    );
    const revokeObjectUrl = vi.fn();
    const NativeURL = globalThis.URL;
    const MockURL = class extends NativeURL {
      static createObjectURL = createObjectUrl;
      static revokeObjectURL = revokeObjectUrl;
    };
    vi.stubGlobal("URL", MockURL);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
      throw new Error("download click failed");
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(new Blob(["video"]), {
        status: 200,
        headers: { "content-type": "video/mp4" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();

    await expect(downloadFile("/api/v1/assets/asset-1/download", "clip.mp4")).rejects.toThrow(
      "download click failed",
    );

    await vi.runAllTimersAsync();

    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:test-download-failure");
  });

  it("accepts 204 No Content as an empty successful response", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/v1/projects")).resolves.toBeNull();
  });

  it("rejects non-absolute API paths before issuing a request", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("api/v1/projects")).rejects.toThrow("absolute paths");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("downloads an authenticated file and revokes its blob URL", async () => {
    const createObjectUrl = vi.fn<(object: Blob | MediaSource) => string>(
      () => "blob:test-download",
    );
    const revokeObjectUrl = vi.fn();
    const NativeURL = globalThis.URL;
    const MockURL = class extends NativeURL {
      static createObjectURL = createObjectUrl;
      static revokeObjectURL = revokeObjectUrl;
    };
    vi.stubGlobal("URL", MockURL);
    useSessionStore.getState().setAdminToken("download-token");

    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(new Blob(["video"]), {
        status: 200,
        headers: { "content-type": "video/mp4" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();

    await downloadFile("/api/v1/assets/asset-1/download", "clip.mp4");

    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-Studio-Admin-Token")).toBe(
      "download-token",
    );
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Accept")).toBe("*/*");
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(createObjectUrl.mock.calls[0]?.[0]).toMatchObject({ type: "video/mp4" });
    expect(click).toHaveBeenCalledTimes(1);

    await vi.runAllTimersAsync();

    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:test-download");
  });
});

describe("apiGetBlob (authenticated media)", () => {
  function binaryResponse(bytes: number, headers: Record<string, string>): Response {
    return new Response(new Uint8Array(bytes), { status: 200, headers });
  }

  it("sends the admin token and returns a Blob typed from the response media type", async () => {
    useSessionStore.getState().setAdminToken("media-token");
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        binaryResponse(8, { "content-type": "image/png; charset=binary", "content-length": "8" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const blob = await apiGetBlob("/api/v1/assets/{asset_id}/download", {
      pathParams: { asset_id: "asset-1" },
      maxBytes: 1024,
    });

    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/api\/v1\/assets\/asset-1\/download$/);
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-Studio-Admin-Token")).toBe("media-token");
    expect(headers.get("Accept")).toBe("*/*");
    expect(blob.size).toBe(8);
    expect(blob.type).toBe("image/png");
  });

  it("rejects a declared Content-Length above the bound without buffering the body", async () => {
    const response = binaryResponse(16, { "content-type": "video/mp4", "content-length": "4096" });
    const cancel = vi.spyOn(response.body!, "cancel");
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response));

    const error = await apiGetBlob("/api/v1/assets/a/download", { maxBytes: 1024 }).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ category: "too_large", retryable: false, status: 200 });
    expect(cancel).toHaveBeenCalled();
  });

  it("stops reading a stream once it exceeds the bound when no length is declared", async () => {
    let pulls = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1;
        controller.enqueue(new Uint8Array(512));
        if (pulls > 50) controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(stream, { status: 200, headers: { "content-type": "video/mp4" } }),
        ),
    );

    await expect(apiGetBlob("/api/v1/assets/a/download", { maxBytes: 1024 })).rejects.toMatchObject(
      { category: "too_large" },
    );
    expect(pulls).toBeLessThan(10);
  });

  it("normalizes JSON errors such as 404 into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "Asset not found" }, 404)),
    );

    await expect(apiGetBlob("/api/v1/assets/a/download", { maxBytes: 1024 })).rejects.toMatchObject(
      { status: 404, message: "Asset not found" },
    );
  });

  it("requires a positive size bound", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGetBlob("/api/v1/assets/a/download", { maxBytes: 0 })).rejects.toThrow(
      "positive maxBytes",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
