import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "@/lib/api/schema";
import { ConnectionList } from "@/features/workspace-overview/components/connection-list";

afterEach(() => cleanup());

const connections: components["schemas"]["ConnectionOut"][] = [
  {
    id: "connection-no-latency",
    name: "Connection without latency",
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
  },
  {
    id: "connection-with-latency",
    name: "Connection with fractional latency",
    provider_type: "muse2api",
    base_url: "https://provider.example.test",
    api_key_masked: "[NOT_SET]",
    proxy_display_host: null,
    timeout_seconds: 30,
    max_concurrency: 2,
    enabled: true,
    health_state: "healthy",
    latency_ms: 12.36,
    capabilities_snapshot: {},
    last_error_redacted: null,
    last_checked_at: null,
  },
];

describe("ConnectionList", () => {
  it("shows an em dash instead of inventing zero latency for null values", () => {
    render(
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
    render(
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
});
