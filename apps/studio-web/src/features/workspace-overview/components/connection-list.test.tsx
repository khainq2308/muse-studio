import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ConnectionSummary } from "@/entities/connection/api";
import { ConnectionList } from "@/features/workspace-overview/components/connection-list";

const connections: ConnectionSummary[] = [
  {
    id: "connection-no-latency",
    name: "Connection without latency",
    provider_type: "muse2api",
    base_url: "https://provider.example.test",
    health_state: "healthy",
    latency_ms: null,
  },
  {
    id: "connection-with-latency",
    name: "Connection with fractional latency",
    provider_type: "muse2api",
    base_url: "https://provider.example.test",
    health_state: "healthy",
    latency_ms: 12.36,
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
        connections={connections}
        isLoading={false}
        isError={false}
        errorMessage="Could not load connections."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("12.4 ms")).toBeInTheDocument();
  });
});
