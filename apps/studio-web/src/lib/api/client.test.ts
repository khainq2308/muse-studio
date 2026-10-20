import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, normalizeApiBaseUrl } from "./client";
import { useSessionStore } from "../../stores/session-store";

afterEach(() => {
  vi.unstubAllGlobals();
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
});

describe("apiGet", () => {
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

  it("uses a generic error for non-JSON upstream error pages", async () => {
    const html =
      "<html><body><h1>502 Bad Gateway</h1><pre>internal proxy details</pre></body></html>";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(html, {
        status: 502,
        headers: { "content-type": "text/html; charset=utf-8" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await apiGet("/api/v1/projects").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 502, detail: null });
    expect((error as Error).message).toBe("API request failed (502)");
    expect((error as Error).message).not.toContain("internal proxy details");
  });

  it("rejects non-absolute API paths before issuing a request", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("api/v1/projects")).rejects.toThrow("absolute paths");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
