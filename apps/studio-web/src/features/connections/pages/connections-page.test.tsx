import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionsPage } from "@/features/connections/pages/connections-page";
import type { components } from "@/lib/api/schema";

type ConnectionOut = components["schemas"]["ConnectionOut"];
type AccountOut = components["schemas"]["AccountOut"];

interface RecordedRequest {
  method: string;
  url: string;
  body: unknown;
}

const connection: ConnectionOut = {
  id: "conn-1",
  name: "Studio gateway",
  provider_type: "muse2api",
  base_url: "https://provider.example.test",
  api_key_masked: "[NOT_SET]",
  proxy_display_host: null,
  timeout_seconds: 30,
  max_concurrency: 2,
  enabled: true,
  health_state: "healthy",
  latency_ms: null,
  capabilities_snapshot: {},
  last_error_redacted: null,
  last_checked_at: null,
};

function makeAccount(overrides: Partial<AccountOut> = {}): AccountOut {
  return {
    id: "acct-1",
    connection_id: "conn-1",
    label: "Account #01",
    api_key_masked: "sk-...abcd",
    proxy_display_host: null,
    priority: 100,
    enabled: true,
    health_state: "healthy",
    latency_ms: null,
    last_error_redacted: null,
    last_checked_at: null,
    ...overrides,
  } as AccountOut;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let requests: RecordedRequest[] = [];
let failMutations = false;

function installFetch(accounts: AccountOut[], connections: ConnectionOut[] = [connection]) {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = request ? request.url : String(input);
      const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
      const rawBody = init?.body ?? (request ? await request.clone().text() : undefined);
      const body = typeof rawBody === "string" && rawBody ? JSON.parse(rawBody) : undefined;
      requests.push({ method, url, body });

      if (method === "GET" && url.endsWith("/api/v1/connections")) return json(connections);
      if (method === "GET" && url.endsWith("/accounts")) return json(accounts);
      if (failMutations) {
        return json({ detail: "Upstream provider rejected the request." }, 422);
      }
      if (method === "POST" && url.endsWith("/accounts")) {
        return json(makeAccount({ id: "acct-new", label: "New account" }), 201);
      }
      if (method === "PATCH") return json(makeAccount({ enabled: false }));
      if (method === "DELETE") return new Response(null, { status: 204 });
      return json(makeAccount());
    }),
  );
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ConnectionsPage />
    </QueryClientProvider>,
  );
}

function mutations(method: string) {
  return requests.filter((request) => request.method === method);
}

beforeEach(() => {
  failMutations = false;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConnectionsPage", () => {
  it("selects a connection card from the keyboard and exposes the selection via aria-pressed", async () => {
    const user = userEvent.setup();
    const backup: ConnectionOut = { ...connection, id: "conn-2", name: "Backup gateway" };
    installFetch([], [connection, backup]);
    renderPage();

    const primary = await screen.findByRole("button", { name: "Chọn kết nối Studio gateway" });
    const secondary = screen.getByRole("button", { name: "Chọn kết nối Backup gateway" });
    expect(primary).toHaveAttribute("aria-pressed", "true");
    expect(secondary).toHaveAttribute("aria-pressed", "false");
    expect(
      await screen.findByRole("heading", { name: "Studio gateway — Nhóm tài khoản" }),
    ).toBeInTheDocument();

    secondary.focus();
    await user.keyboard("{Enter}");
    expect(secondary).toHaveAttribute("aria-pressed", "true");
    expect(primary).toHaveAttribute("aria-pressed", "false");
    expect(
      await screen.findByRole("heading", { name: "Backup gateway — Nhóm tài khoản" }),
    ).toBeInTheDocument();

    primary.focus();
    await user.keyboard(" ");
    expect(primary).toHaveAttribute("aria-pressed", "true");
  });

  it("creates a pool account with the expected body and clears the API key afterwards", async () => {
    const user = userEvent.setup();
    installFetch([]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Thêm tài khoản vào nhóm/i }));
    const form = screen.getByRole("form", { name: "Thêm tài khoản vào nhóm" });
    await user.type(within(form).getByLabelText("Nhãn tài khoản"), "New account");
    await user.type(within(form).getByLabelText("API key của tài khoản"), "sk-secret-value");
    await user.click(within(form).getByRole("button", { name: "Lưu tài khoản" }));

    await waitFor(() => expect(mutations("POST")).toHaveLength(1));
    const [createRequest] = mutations("POST");
    expect(createRequest.url).toMatch(/\/api\/v1\/connections\/conn-1\/accounts$/);
    expect(createRequest.body).toEqual({
      label: "New account",
      api_key: "sk-secret-value",
      proxy_url: null,
      priority: 100,
      enabled: true,
    });

    await waitFor(() =>
      expect(
        screen.queryByRole("form", { name: "Thêm tài khoản vào nhóm" }),
      ).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: /Thêm tài khoản vào nhóm/i }));
    expect(screen.getByLabelText("API key của tài khoản")).toHaveValue("");
  });

  it("clears the typed API key when the add-account form is cancelled", async () => {
    const user = userEvent.setup();
    installFetch([]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Thêm tài khoản vào nhóm/i }));
    await user.type(screen.getByLabelText("API key của tài khoản"), "sk-secret-value");
    await user.click(screen.getByRole("button", { name: "Hủy" }));
    await user.click(screen.getByRole("button", { name: /Thêm tài khoản vào nhóm/i }));

    expect(screen.getByLabelText("API key của tài khoản")).toHaveValue("");
  });

  it("sends PATCH {enabled:false} when disabling an account", async () => {
    const user = userEvent.setup();
    installFetch([makeAccount()]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Vô hiệu hóa" }));

    await waitFor(() => expect(mutations("PATCH")).toHaveLength(1));
    const [patchRequest] = mutations("PATCH");
    expect(patchRequest.url).toMatch(/\/api\/v1\/connections\/conn-1\/accounts\/acct-1$/);
    expect(patchRequest.body).toEqual({ enabled: false });
  });

  it("requires confirmation before deleting an account", async () => {
    const user = userEvent.setup();
    installFetch([makeAccount()]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Xóa/i }));
    let dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent('Xóa tài khoản "Account #01" khỏi nhóm?');
    expect(dialog).toHaveTextContent(/nhập lại API key/i);

    await user.click(within(dialog).getByRole("button", { name: "Hủy" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(mutations("DELETE")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: /Xóa/i }));
    dialog = screen.getByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Xóa tài khoản" }));

    await waitFor(() => expect(mutations("DELETE")).toHaveLength(1));
    expect(mutations("DELETE")[0].url).toMatch(/\/api\/v1\/connections\/conn-1\/accounts\/acct-1$/);
  });

  it("shows an alert with the validation detail when an account mutation fails", async () => {
    const user = userEvent.setup();
    installFetch([makeAccount()]);
    failMutations = true;
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Vô hiệu hóa" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Không thể disable account/i);
    expect(alert).toHaveTextContent(/Upstream provider rejected the request/i);
  });

  it("shows an alert when testing a connection fails", async () => {
    const user = userEvent.setup();
    installFetch([]);
    failMutations = true;
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Kiểm tra kết nối/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Không thể test connection/i);
  });

  it("keeps the base URL empty by default and clamps connection limits to the API schema", async () => {
    const user = userEvent.setup();
    installFetch([]);
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Kết nối mới/i }));
    const form = screen.getByRole("form", { name: "Tạo kết nối nhà cung cấp" });
    const baseUrl = within(form).getByLabelText("Base URL");
    expect(baseUrl).toHaveValue("");
    expect(baseUrl).toBeRequired();
    expect(within(form).getByRole("button", { name: "Tạo kết nối" })).toBeDisabled();

    const concurrency = within(form).getByLabelText("Số tác vụ đồng thời tối đa");
    expect(concurrency).toHaveAttribute("max", "20");
    await user.clear(concurrency);
    await user.type(concurrency, "50");
    expect(concurrency).toHaveValue(20);

    const timeout = within(form).getByLabelText("Thời gian chờ (giây)");
    expect(timeout).toHaveAttribute("max", "120");
  });
});
