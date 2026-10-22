import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  params: { projectId: "project-a", module: "queue-monitor" },
  mounts: [] as string[],
  projects: {
    isPending: false,
    isSuccess: true,
    data: [{ id: "project-a" }, { id: "project-b" }] as Array<{ id: string }> | undefined,
  },
}));

vi.mock("@tanstack/react-router", () => ({
  useParams: () => mocks.params,
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

vi.mock("@/entities/project/api", () => ({
  useProjects: () => mocks.projects,
}));

vi.mock("@/features/queue-monitor", async () => {
  const { useEffect } = await import("react");
  return {
    default: function QueueMonitorStub({ projectId }: { projectId: string }) {
      useEffect(() => {
        mocks.mounts.push(projectId);
      }, []); // eslint-disable-line react-hooks/exhaustive-deps
      return <div data-testid="queue-stub">{projectId}</div>;
    },
  };
});

import ProjectModuleRoute from "@/app/project-module-route";

beforeEach(() => {
  mocks.params = { projectId: "project-a", module: "queue-monitor" };
  mocks.mounts = [];
  mocks.projects = {
    isPending: false,
    isSuccess: true,
    data: [{ id: "project-a" }, { id: "project-b" }],
  };
});

afterEach(() => cleanup());

describe("ProjectModuleRoute", () => {
  it("remounts the queue monitor when the project changes", () => {
    const { rerender, getByTestId } = render(<ProjectModuleRoute />);
    mocks.params = { projectId: "project-b", module: "queue-monitor" };
    rerender(<ProjectModuleRoute />);

    expect(getByTestId("queue-stub").textContent).toBe("project-b");
    expect(mocks.mounts).toEqual(["project-a", "project-b"]);
  });

  it("shows a not-found page instead of an empty module for an unknown project", () => {
    mocks.params = { projectId: "missing-project", module: "queue-monitor" };
    render(<ProjectModuleRoute />);

    expect(screen.getByRole("heading", { name: "Project not found" })).toBeTruthy();
    expect(screen.getByText(/missing-project/)).toBeTruthy();
    expect(screen.queryByTestId("queue-stub")).toBeNull();
  });

  it("waits for the project list before rendering the module", () => {
    mocks.projects = { isPending: true, isSuccess: false, data: undefined };
    render(<ProjectModuleRoute />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading project...");
    expect(screen.queryByTestId("queue-stub")).toBeNull();
  });

  it("does not claim the project is missing when the project list cannot be read", () => {
    mocks.projects = { isPending: false, isSuccess: false, data: undefined };
    render(<ProjectModuleRoute />);

    expect(screen.queryByRole("heading", { name: "Project not found" })).toBeNull();
    expect(screen.getByTestId("queue-stub")).toBeTruthy();
  });
});
