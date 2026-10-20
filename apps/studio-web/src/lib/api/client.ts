import { useSessionStore } from "@/lib/stores/session-store";

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue>;

export interface ApiRequestOptions extends Omit<RequestInit, "body" | "headers"> {
  headers?: HeadersInit;
  query?: QueryParams;
  body?: unknown;
}

export interface ApiErrorBody {
  status: number;
  detail: unknown;
  category?: string;
  retryable?: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (isRecord(detail)) {
    const message = [detail.message, detail.msg, detail.error].find(
      (value): value is string => typeof value === "string" && value.trim().length > 0,
    );
    if (message) return message;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!isRecord(item)) return typeof item === "string" ? item : "";
        return typeof item.msg === "string"
          ? item.msg
          : typeof item.message === "string"
            ? item.message
            : "";
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join("; ");
  }
  return `API request failed (${status})`;
}

export class ApiError extends Error implements ApiErrorBody {
  readonly status: number;
  readonly detail: unknown;
  readonly category?: string;
  readonly retryable?: boolean;

  constructor(body: ApiErrorBody) {
    super(errorMessage(body.detail, body.status));
    this.name = "ApiError";
    this.status = body.status;
    this.detail = body.detail;
    this.category = body.category;
    this.retryable = body.retryable;
  }
}

/** A dot or slash means same-origin; absolute HTTP(S) URLs support local development. */
export function normalizeApiBaseUrl(value: string | undefined): string {
  const trimmed = (value ?? "").trim();
  if (!trimmed || trimmed === "." || trimmed === "/") return "";

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error("VITE_ORCHESTRATOR_URL must be '.', '/', empty, or an absolute HTTP(S) URL.");
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("VITE_ORCHESTRATOR_URL must use HTTP or HTTPS.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("VITE_ORCHESTRATOR_URL must not include credentials, a query, or a fragment.");
  }

  return parsed.href.replace(/\/+$/, "");
}

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_ORCHESTRATOR_URL);

export function encodePathSegment(segment: string): string {
  if (segment === "." || segment === "..") {
    throw new Error("API path segments must not be '.' or '..'.");
  }
  return encodeURIComponent(segment);
}

function buildUrl(path: string, query?: QueryParams): string {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("API paths must be absolute paths beginning with one '/'.");
  }
  if (path.includes("\\") || path.includes("?") || path.includes("#")) {
    throw new Error("API paths must not include backslashes, queries, or fragments.");
  }
  for (const segment of path.split("/")) {
    let decodedSegment: string;
    try {
      decodedSegment = decodeURIComponent(segment);
    } catch {
      throw new Error("API paths must use valid URL encoding.");
    }
    if (decodedSegment === "." || decodedSegment === "..") {
      throw new Error("API paths must not contain dot segments.");
    }
  }

  const url = new URL(API_BASE_URL + path, window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url.toString();
}

function isBodyInit(body: unknown): body is BodyInit {
  return (
    typeof body === "string" ||
    body instanceof Blob ||
    body instanceof FormData ||
    body instanceof URLSearchParams ||
    body instanceof ArrayBuffer ||
    ArrayBuffer.isView(body)
  );
}

async function send(path: string, options: ApiRequestOptions = {}): Promise<Response> {
  const { query, headers: inputHeaders, body, ...init } = options;
  const headers = new Headers(inputHeaders);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  const token = useSessionStore.getState().adminToken;
  if (token) headers.set("X-Studio-Admin-Token", token);
  else headers.delete("X-Studio-Admin-Token");

  let requestBody: BodyInit | undefined;
  if (body !== undefined) {
    if (isBodyInit(body)) {
      requestBody = body;
    } else {
      requestBody = JSON.stringify(body);
      if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    }
  }

  return fetch(buildUrl(path, query), { ...init, headers, body: requestBody });
}

function isJsonContentType(contentType: string): boolean {
  const mediaType = contentType.split(";")[0]?.trim().toLowerCase() ?? "";
  return mediaType === "application/json" || mediaType.endsWith("+json");
}

async function parseResponse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") ?? "";
  if (!isJsonContentType(contentType)) {
    throw new ApiError({
      status: response.status,
      detail: `Expected JSON API response (${response.status}).`,
      category: "invalid_response",
      retryable: false,
    });
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError({
      status: response.status,
      detail: `Invalid JSON API response (${response.status}).`,
      category: "invalid_response",
      retryable: false,
    });
  }
}

async function throwApiError(response: Response): Promise<never> {
  // Only trust structured error fields from JSON. Proxies often return HTML for 502/503;
  // forwarding that body into Error.message would leak a raw upstream page into future UI.
  const contentType = response.headers.get("content-type") ?? "";
  let payload: unknown = null;
  if (isJsonContentType(contentType)) {
    try {
      payload = await response.json();
    } catch {
      // Invalid error JSON should degrade to a generic ApiError, not mask the HTTP status.
    }
  }
  const record = isRecord(payload) ? payload : undefined;
  const detail = record && "detail" in record ? record.detail : payload;
  const category = typeof record?.category === "string" ? record.category : undefined;
  const retryable = typeof record?.retryable === "boolean" ? record.retryable : undefined;
  throw new ApiError({ status: response.status, detail, category, retryable });
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (!response.ok) return throwApiError(response);
  return (await parseResponse(response)) as T;
}

export function apiGet<T>(path: string, options: Omit<ApiRequestOptions, "method" | "body"> = {}) {
  return apiRequest<T>(path, { ...options, method: "GET" });
}

export function apiPost<T>(
  path: string,
  body?: unknown,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
) {
  return apiRequest<T>(path, { ...options, method: "POST", body });
}

export function apiPut<T>(
  path: string,
  body?: unknown,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
) {
  return apiRequest<T>(path, { ...options, method: "PUT", body });
}

export function apiPatch<T>(
  path: string,
  body?: unknown,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
) {
  return apiRequest<T>(path, { ...options, method: "PATCH", body });
}

export function apiDelete<T>(
  path: string,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
) {
  return apiRequest<T>(path, { ...options, method: "DELETE" });
}

/** Download a file with the same authentication and error normalization as JSON requests. */
export async function downloadFile(
  path: string,
  filename: string,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
): Promise<void> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "*/*");
  const response = await send(path, { ...options, method: "GET", headers });
  if (!response.ok) return throwApiError(response);

  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Let the browser consume the Blob URL before revoking it. The response is currently
  // materialized as a Blob for cross-browser authenticated downloads; a streaming-to-disk
  // path can be added for very large media in a later media-workflow phase.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}
