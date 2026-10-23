import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/lib/api/schema";
import { ConnectionList } from "@/features/workspace-overview/components/connection-list";

afterEach(() => cleanup());

function renderWithQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

type ConnectionOut = components["schemas"]["ConnectionOut"];

function makeConnection(overrides: Partial<ConnectionOut> = {}): ConnectionOut {
  return {
    id: "connection-default",
    name: "Default connection",
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
    ...overrides,
  };
}

const connections: ConnectionOut[] = [
  makeConnection({
    id: "connection-no-latency",
    name: "Connection without latency",
  }),
  makeConnection({
    id: "connection-with-latency",
    name: "Connection with fractional latency",
    latency_ms: 12.36,
  }),
];

describe("ConnectionList", () => {
  it("shows an em dash instead of inventing zero latency for null values", () => {
    renderWithQueryClient(
      <ConnectionList
        connections={connections}
        isLoading={false}
        isError={false}
        errorMessage="Could not load connections."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0 ms")).not.toBeInTheDocument();
  });

  it("limits fractional latency to one decimal place without trailing zeroes", () => {
    renderWithQueryClient(
      <ConnectionList
        connections={connections.slice(1)}
        isLoading={false}
        isError={false}
        errorMessage="Could not load connections."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("12.4 ms")).toBeInTheDocument();
  });

  it("keeps the base URL empty, mirrors API limits, and clears the API key on cancel", async () => {
    const user = userEvent.setup();
    renderWithQueryClient(
      <ConnectionList
        connections={[]}
        isLoading={false}
        isError={false}
        errorMessage="Could not load connections."
        onRetry={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /Add provider/i }));
    const form = screen.getByRole("form", { name: "Create provider connection" });
    const baseUrl = within(form).getByLabelText("Base URL");
    expect(baseUrl).toHaveValue("");
    expect(baseUrl).toBeRequired();
    expect(within(form).getByLabelText("Max concurrency")).toHaveAttribute("max", "20");
    expect(within(form).getByLabelText("Timeout (seconds)")).toHaveAttribute("max", "120");

    const timeout = within(form).getByLabelText("Timeout (seconds)");
    await user.clear(timeout);
    await user.type(timeout, "500");
    expect(timeout).toHaveValue(120);

    await user.type(
      within(form).getByLabelText("API key (optional if using account pool)"),
      "sk-secret",
    );
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: /Add provider/i }));
    expect(screen.getByLabelText("API key (optional if using account pool)")).toHaveValue("");
  });
});
