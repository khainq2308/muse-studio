import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryState } from "@/components/shared/query-state";

const defaults = {
  isLoading: false,
  isError: false,
  isEmpty: false,
  loadingMessage: "Loading projects...",
  errorMessage: "Could not load projects.",
  emptyTitle: "No projects yet",
  emptyDescription: "Create a project to get started.",
  onRetry: vi.fn(),
  children: <p>Project data</p>,
};

describe("QueryState", () => {
  it("renders loading state", () => {
    render(<QueryState {...defaults} isLoading />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading projects...");
  });

  it("renders an error with a working retry action", () => {
    const onRetry = vi.fn();
    render(<QueryState {...defaults} isError onRetry={onRetry} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not load projects.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("renders the empty state", () => {
    render(<QueryState {...defaults} isEmpty />);

    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(screen.getByText("Create a project to get started.")).toBeInTheDocument();
  });

  it("renders data when no special state applies", () => {
    render(<QueryState {...defaults} />);

    expect(screen.getByText("Project data")).toBeInTheDocument();
  });
});
