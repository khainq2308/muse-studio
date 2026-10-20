import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiPut,
  downloadFile,
  encodePathSegment,
  normalizeApiBaseUrl,
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

  it("rejects protocol-relative and non-HTTP URLs", () => {
    expect(() => normalizeApiBaseUrl("//evil.test")).toThrow();
    expect(() => normalizeApiBaseUrl("file:///etc/passwd")).toThrow();
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

    await apiGet<Array<{ id: string }>>("/api/v1/projects", {
      query: { limit: 10, archived: false, omitted: null },
    });

    const [requestedUrl, init] = fetchMock.mock.calls[0]!;
    expect(String(requestedUrl)).toContain("/api/v1/projects?limit=10&archived=false");
    expect(new Headers(init?.headers).get("X-Studio-Admin-Token")).toBe("test-admin-token");
    expect(window.localStorage.length).toBe(0);
  });

  it("passes AbortSignal through to fetch", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/api/v1/projects", { signal: controller.signal });

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("sends the expected HTTP methods and JSON bodies", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ id: "created" }))
      .mockResolvedValueOnce(jsonResponse({ id: "replaced" }))
      .mockResolvedValueOnce(jsonResponse({ id: "patched" }))
      .mockResolvedValueOnce(jsonResponse({ id: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);

    await apiPost("/api/v1/projects", { name: "Created" });
    await apiPut("/api/v1/projects/project-1", { name: "Replaced" });
    await apiPatch("/api/v1/projects/project-1", { name: "Patched" });
    await apiDelete("/api/v1/projects/project-1");

    expect(fetchMock.mock.calls.map(([, init]) => init?.method)).toEqual([
      "POST",
      "PUT",
      "PATCH",
      "DELETE",
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ name: "Created" });
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

    await expect(apiGet("/api/v1/example")).resolves.toEqual({ status: "ok" });
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

  it("encodes API path parameters as one safe segment", () => {
    expect(encodePathSegment("project/42 with space")).toBe("project%2F42%20with%20space");
    expect(() => encodePathSegment("..")).toThrow("must not be '.' or '..'");
  });

  it.each(["/api/v1/projects/../connections", "/api/v1/projects/%2e%2e/connections"])(
    "rejects traversal path %s before fetching",
    async (path) => {
      const fetchMock = vi.fn<typeof fetch>();
      vi.stubGlobal("fetch", fetchMock);

      await expect(apiGet(path)).rejects.toThrow("dot segments");
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

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

  it("accepts 204 No Content as an empty successful response", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/v1/projects")).resolves.toBeNull();
  });

  it("rejects non-absolute API paths before issuing a request", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("api/v1/projects")).rejects.toThrow("absolute paths");
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
