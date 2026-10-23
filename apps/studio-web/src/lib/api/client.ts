import type { paths } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type QueryValue = string | number | boolean | null | undefined;
export type QueryParams = Record<string, QueryValue>;

type HttpMethod = "get" | "post" | "put" | "patch" | "delete";

/** Paths with a real GET operation in the generated OpenAPI contract. */
export type ApiGetPath = {
  [Path in keyof paths]: paths[Path] extends { get: infer Operation }
    ? [JsonSuccessBody<NonNullable<Operation>>] extends [never]
      ? never
      : Path
    : never;
}[keyof paths] &
  string;

type ApiOperation<
  Path extends keyof paths,
  Method extends HttpMethod,
> = Method extends keyof paths[Path] ? NonNullable<paths[Path][Method]> : never;

type ApiPathParams<Path extends ApiGetPath> = Path extends keyof paths
  ? ApiOperation<Path, "get"> extends { parameters: { path: infer Params } }
    ? Params
    : Record<never, never>
  : Record<never, never>;

type ApiQueryParams<Path extends ApiGetPath> = Path extends keyof paths
  ? ApiOperation<Path, "get"> extends { parameters: { query?: infer Query } }
    ? Exclude<Query, undefined>
    : never
  : never;

type ApiGetOptions<Path extends ApiGetPath> = Omit<
  ApiRequestOptions,
  "method" | "body" | "pathParams" | "query"
> & {
  query?: ApiQueryParams<Path>;
};
type ApiGetArguments<Path extends ApiGetPath> = keyof ApiPathParams<Path> extends never
  ? [options?: ApiGetOptions<Path>]
  : [options: ApiGetOptions<Path> & { pathParams: ApiPathParams<Path> }];

type ApiPostPath = {
  [Path in keyof paths]: paths[Path] extends { post: infer Operation }
    ? [JsonSuccessBody<NonNullable<Operation>>] extends [never]
      ? never
      : Path
    : never;
}[keyof paths] &
  string;

type ApiPostPathParams<Path extends ApiPostPath> = Path extends keyof paths
  ? ApiOperation<Path, "post"> extends { parameters: { path: infer Params } }
    ? Params
    : Record<never, never>
  : Record<never, never>;

type ApiPostQueryParams<Path extends ApiPostPath> = Path extends keyof paths
  ? ApiOperation<Path, "post"> extends { parameters: { query?: infer Query } }
    ? Exclude<Query, undefined>
    : never
  : never;

type JsonRequestBody<Operation> = Operation extends { requestBody: infer RequestBody }
  ? RequestBody extends { content: infer Content }
    ? Content extends { "application/json": infer Body }
      ? Body
      : never
    : never
  : never;

type ApiPostOptions<Path extends ApiPostPath> = Omit<
  ApiRequestOptions,
  "method" | "body" | "pathParams" | "query"
> & {
  query?: ApiPostQueryParams<Path>;
  pathParams?: ApiPostPathParams<Path>;
};

type ApiPostArguments<Path extends ApiPostPath> = [
  JsonRequestBody<ApiOperation<Path, "post">>,
] extends [never]
  ? keyof ApiPostPathParams<Path> extends never
    ? [body?: undefined, options?: ApiPostOptions<Path>]
    : [body: undefined, options: ApiPostOptions<Path> & { pathParams: ApiPostPathParams<Path> }]
  : keyof ApiPostPathParams<Path> extends never
    ? [body: JsonRequestBody<ApiOperation<Path, "post">>, options?: ApiPostOptions<Path>]
    : [
        body: JsonRequestBody<ApiOperation<Path, "post">>,
        options: ApiPostOptions<Path> & { pathParams: ApiPostPathParams<Path> },
      ];

type JsonSuccessBody<Operation> = Operation extends { responses: infer Responses }
  ? {
      [Status in keyof Responses]: Status extends string | number
        ? `${Status}` extends `2${string}`
          ? Responses[Status] extends { content: infer Content }
            ? Content extends { "application/json": infer Body }
              ? Body
              : never
            : never
          : never
        : never;
    }[keyof Responses]
  : never;

/** The successful JSON body inferred from a generated OpenAPI path + method. */
export type ApiJsonResponse<
  Path extends string,
  Method extends HttpMethod,
> = Path extends keyof paths
  ? Method extends keyof paths[Path]
    ? JsonSuccessBody<NonNullable<paths[Path][Method]>>
    : never
  : never;

export interface ApiRequestOptions extends Omit<RequestInit, "body" | "headers"> {
  headers?: HeadersInit;
  query?: QueryParams;
  /** Values substituted into OpenAPI-style {path_parameter} segments. */
  pathParams?: Record<string, string | number>;
  body?: unknown;
  /**
   * Abort the request (including reading the response body) after this many milliseconds.
   * Defaults to {@link DEFAULT_REQUEST_TIMEOUT_MS}, or {@link LONG_REQUEST_TIMEOUT_MS} for
   * binary/multipart uploads and file downloads. A timeout rejects with a retryable
   * `ApiError` whose category is `timeout`; a caller-provided signal still cancels normally.
   */
  timeoutMs?: number;
}

/** Default bound for JSON/text API calls so a hung upstream cannot block the UI indefinitely. */
export const DEFAULT_REQUEST_TIMEOUT_MS = 20_000;
/** Uploads, backup restores and file downloads can legitimately take minutes. */
export const LONG_REQUEST_TIMEOUT_MS = 10 * 60_000;

export interface ApiErrorBody {
  status: number;
  detail: unknown;
  category?: string;
  retryable?: boolean;
  /** Parsed Retry-After header in milliseconds, when the server provided one. */
  retryAfterMs?: number;
}

/** Parse an HTTP Retry-After value (delta-seconds or HTTP-date) into milliseconds. */
export function parseRetryAfter(
  value: string | null,
  now: number = Date.now(),
): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed) * 1000;
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return undefined;
  return Math.max(0, date - now);
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
        const message = !isRecord(item)
          ? typeof item === "string"
            ? item
            : ""
          : typeof item.msg === "string"
            ? item.msg
            : typeof item.message === "string"
              ? item.message
              : "";
        return message.trim() ? message : "";
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
  readonly retryAfterMs?: number;

  constructor(body: ApiErrorBody) {
    super(errorMessage(body.detail, body.status));
    this.name = "ApiError";
    this.status = body.status;
    this.detail = body.detail;
    this.category = body.category;
    this.retryable = body.retryable;
    this.retryAfterMs = body.retryAfterMs;
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
  if (parsed.pathname !== "/") {
    throw new Error("VITE_ORCHESTRATOR_URL must contain an origin only, without a path.");
  }

  return parsed.origin;
}

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_ORCHESTRATOR_URL);

export function encodePathSegment(segment: string): string {
  if (segment === "." || segment === ".." || segment.includes("/") || segment.includes("\\")) {
    throw new Error("API path segments must not contain path separators or dot segments.");
  }
  return encodeURIComponent(segment);
}

function buildUrl(
  path: string,
  query?: QueryParams,
  pathParams?: Record<string, string | number>,
): string {
  const matches = [...path.matchAll(/\{([^{}]+)\}/g)];
  const names = [...new Set(matches.map((match) => match[1]!))];

  if (matches.length > 0) {
    if (!pathParams) {
      throw new Error("API path parameters are required for this endpoint.");
    }
    const missing = names.filter((name) => pathParams[name] === undefined);
    if (missing.length > 0) {
      throw new Error(`Missing API path parameter(s): ${missing.join(", ")}.`);
    }
    const unexpected = Object.keys(pathParams).filter((name) => !names.includes(name));
    if (unexpected.length > 0) {
      throw new Error(`Unexpected API path parameter(s): ${unexpected.join(", ")}.`);
    }
    const encodePathParam = (_match: string, name: string) =>
      encodePathSegment(String(pathParams[name]!));
    path = path.replace(/\{([^{}]+)\}/g, encodePathParam);
  } else if (pathParams && Object.keys(pathParams).length > 0) {
    throw new Error("Path parameters were supplied for an endpoint without path parameters.");
  }

  if (/[{}]/.test(path)) {
    throw new Error("API path contains a malformed path parameter template.");
  }

  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error("API paths must be absolute paths beginning with one '/'.");
  }
  if (path.includes("//") || path.includes("\\") || path.includes("?") || path.includes("#")) {
    throw new Error(
      "API paths must not contain repeated separators, backslashes, queries, or fragments.",
    );
  }
  for (const segment of path.split("/")) {
    let decodedSegment: string;
    try {
      decodedSegment = decodeURIComponent(segment);
    } catch {
      throw new Error("API paths must use valid URL encoding.");
    }
    if (
      decodedSegment === "." ||
      decodedSegment === ".." ||
      decodedSegment.includes("/") ||
      decodedSegment.includes("\\")
    ) {
      throw new Error("API paths must not contain dot segments or encoded path separators.");
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

function requestTimeoutError(timeoutMs: number): ApiError {
  return new ApiError({
    status: 0,
    detail: `The API request timed out after ${Math.ceil(timeoutMs / 1000)} seconds.`,
    category: "timeout",
    retryable: true,
  });
}

/**
 * Run one request — fetch plus response handling — under a combined caller/timeout signal.
 * The work is also raced against the signal so a transport that ignores AbortSignal (or a
 * stalled response body) still settles when the deadline passes.
 */
async function execute<T>(
  path: string,
  options: ApiRequestOptions,
  defaultTimeoutMs: number,
  handle: (response: Response) => Promise<T>,
): Promise<T> {
  const {
    query,
    pathParams,
    headers: inputHeaders,
    body,
    signal: callerSignal,
    timeoutMs: requestedTimeoutMs,
    ...init
  } = options;
  const url = buildUrl(path, query, pathParams);
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

  const timeoutMs =
    requestedTimeoutMs ??
    (requestBody instanceof FormData || requestBody instanceof Blob
      ? LONG_REQUEST_TIMEOUT_MS
      : defaultTimeoutMs);
  const controller = new AbortController();
  let timedOut = false;
  let rejectOnAbort: (reason: unknown) => void = () => {};
  const aborted = new Promise<never>((_resolve, reject) => {
    rejectOnAbort = reject;
  });
  const abort = (reason: unknown) => {
    if (controller.signal.aborted) return;
    controller.abort(reason);
    rejectOnAbort(reason);
  };
  const onCallerAbort = () =>
    abort(callerSignal?.reason ?? new DOMException("The request was aborted.", "AbortError"));
  const timer = setTimeout(() => {
    timedOut = true;
    abort(requestTimeoutError(timeoutMs));
  }, timeoutMs);
  if (callerSignal?.aborted) onCallerAbort();
  else callerSignal?.addEventListener("abort", onCallerAbort, { once: true });

  try {
    const work = (async () => {
      const response = await fetch(url, {
        ...init,
        headers,
        body: requestBody,
        signal: controller.signal,
      });
      return handle(response);
    })();
    // Avoid an unhandled rejection when the abort race wins before `work` settles.
    work.catch(() => undefined);
    return await Promise.race([work, aborted]);
  } catch (error) {
    if (timedOut) throw requestTimeoutError(timeoutMs);
    throw error;
  } finally {
    clearTimeout(timer);
    callerSignal?.removeEventListener("abort", onCallerAbort);
  }
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
  const retryAfterMs = parseRetryAfter(response.headers.get("retry-after"));
  throw new ApiError({ status: response.status, detail, category, retryable, retryAfterMs });
}

export function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  return execute(path, options, DEFAULT_REQUEST_TIMEOUT_MS, async (response) => {
    if (!response.ok) return throwApiError(response);
    return (await parseResponse(response)) as T;
  });
}

export function apiGet<Path extends ApiGetPath>(
  path: Path,
  ...args: ApiGetArguments<Path>
): Promise<ApiJsonResponse<Path, "get">> {
  const [options = {}] = args as [ApiRequestOptions?];
  return apiRequest<ApiJsonResponse<Path, "get">>(path, { ...options, method: "GET" });
}

/** Read an explicitly textual API endpoint such as a Markdown export or Prometheus metrics. */
export function apiGetText(
  path: string,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
): Promise<string> {
  const headers = new Headers(options.headers);
  if (!headers.has("Accept")) headers.set("Accept", "text/plain, text/markdown");
  return execute(
    path,
    { ...options, method: "GET", headers },
    DEFAULT_REQUEST_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) return throwApiError(response);
      if (response.status === 204) return "";

      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.toLowerCase().startsWith("text/")) {
        throw new ApiError({
          status: response.status,
          detail: `Expected a text API response (${response.status}).`,
          category: "invalid_response",
          retryable: false,
        });
      }
      return response.text();
    },
  );
}

export function apiPost<Path extends ApiPostPath>(
  path: Path,
  ...args: ApiPostArguments<Path>
): Promise<ApiJsonResponse<Path, "post">> {
  const [body, options = {}] = args as unknown as [unknown?, ApiRequestOptions?];
  return apiRequest<ApiJsonResponse<Path, "post">>(path, {
    ...options,
    method: "POST",
    body,
  });
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

export interface ApiGetBlobOptions extends Omit<ApiRequestOptions, "method" | "body"> {
  /**
   * Reject (category `too_large`) instead of buffering a body larger than this many bytes.
   * The whole response is held in memory, so callers must choose a bound that fits a
   * single on-demand preview.
   */
  maxBytes: number;
}

function mediaTooLargeError(status: number, maxBytes: number): ApiError {
  return new ApiError({
    status,
    detail: `The file is larger than the ${Math.floor(maxBytes / (1024 * 1024))} MB in-browser preview limit.`,
    category: "too_large",
    retryable: false,
  });
}

/** `image/png; charset=binary` -> `image/png`; empty when the server sent no type. */
function mediaTypeOf(response: Response): string {
  return (response.headers.get("content-type") ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
}

/**
 * Fetch an authenticated binary resource (for example `GET /api/v1/assets/{asset_id}/download`)
 * into a `Blob` whose `type` is the response media type. The admin token is sent as a header,
 * which a plain `<img src>`/`<video src>` cannot do; callers turn the Blob into an object URL
 * and must revoke it. Bodies above `maxBytes` are rejected from `Content-Length` before they
 * are read, and the stream is cancelled as soon as the running total exceeds the bound when
 * the length is missing or wrong.
 */
export function apiGetBlob(path: string, options: ApiGetBlobOptions): Promise<Blob> {
  const { maxBytes, ...requestOptions } = options;
  if (!Number.isFinite(maxBytes) || maxBytes <= 0) {
    return Promise.reject(new Error("apiGetBlob requires a positive maxBytes bound."));
  }
  const headers = new Headers(requestOptions.headers);
  headers.set("Accept", "*/*");
  return execute(
    path,
    { ...requestOptions, method: "GET", headers },
    LONG_REQUEST_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) return throwApiError(response);
      const type = mediaTypeOf(response);
      const declared = Number(response.headers.get("content-length"));
      if (Number.isFinite(declared) && declared > maxBytes) {
        await response.body?.cancel().catch(() => undefined);
        throw mediaTooLargeError(response.status, maxBytes);
      }
      if (!response.body) {
        const blob = await response.blob();
        if (blob.size > maxBytes) throw mediaTooLargeError(response.status, maxBytes);
        return type && blob.type !== type ? new Blob([blob], { type }) : blob;
      }

      const reader = response.body.getReader();
      const chunks: Uint8Array<ArrayBuffer>[] = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > maxBytes) {
          await reader.cancel().catch(() => undefined);
          throw mediaTooLargeError(response.status, maxBytes);
        }
        chunks.push(value as Uint8Array<ArrayBuffer>);
      }
      return new Blob(chunks, { type });
    },
  );
}

/** Download a file with the same authentication and error normalization as JSON requests. */
export async function downloadFile(
  path: string,
  filename: string,
  options: Omit<ApiRequestOptions, "method" | "body"> = {},
): Promise<void> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "*/*");
  const blob = await execute(
    path,
    { ...options, method: "GET", headers },
    LONG_REQUEST_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) return throwApiError(response);
      return response.blob();
    },
  );

  const objectUrl = URL.createObjectURL(blob);
  let anchor: HTMLAnchorElement | null = null;
  try {
    anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.hidden = true;
    document.body.append(anchor);
    anchor.click();
  } finally {
    try {
      anchor?.remove();
    } finally {
      // Let the browser consume the Blob URL before revoking it. The response is currently
      // materialized as a Blob for cross-browser authenticated downloads; a streaming-to-disk
      // path can be added for very large media in a later media-workflow phase.
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    }
  }
}
