import { cleanup, render, screen, within } from "@testing-library/react";
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
  useSearch: () => ({}),
  useNavigate: () => () => Promise.resolve(),
  Link: ({
    children,
    to,
    params,
    "aria-current": ariaCurrent,
  }: {
    children: unknown;
    to: string;
    params?: { projectId: string; module: string };
    "aria-current"?: "page";
  }) => (
    <a
      href={params ? `/projects/${params.projectId}/${params.module}` : to}
      aria-current={ariaCurrent}
    >
      {children as string}
    </a>
  ),
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

vi.mock("@/features/shot-board", () => ({
  default: function ShotBoardStub({ projectId }: { projectId: string }) {
    return <div data-testid="shot-board-stub">{projectId}</div>;
  },
}));

vi.mock("@/features/script-studio", () => ({
  default: function ScriptStudioStub({ projectId }: { projectId: string }) {
    return <div data-testid="script-studio-stub">{projectId}</div>;
  },
}));

vi.mock("@/features/workflow-canvas", () => ({
  default: function WorkflowCanvasStub({ projectId }: { projectId: string }) {
    return <div data-testid="workflow-canvas-stub">{projectId}</div>;
  },
}));

vi.mock("@/features/timeline-editor", () => ({
  default: function TimelineEditorStub({ projectId }: { projectId: string }) {
    return <div data-testid="timeline-editor-stub">{projectId}</div>;
  },
}));

vi.mock("@/features/projects", () => ({
  ProjectSettingsPage: function ProjectSettingsStub({ projectId }: { projectId: string }) {
    return <div data-testid="project-settings-stub">{projectId}</div>;
  },
}));

vi.mock("@/features/system-ops", () => ({
  ProjectDataPage: function ProjectDataStub({ projectId }: { projectId: string }) {
    return <div data-testid="project-data-stub">{projectId}</div>;
  },
}));

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

  it("renders the Shot Board module with project navigation marking the active module", () => {
    mocks.params = { projectId: "project-a", module: "shot-board" };
    render(<ProjectModuleRoute />);

    expect(screen.getByTestId("shot-board-stub").textContent).toBe("project-a");
    expect(screen.queryByTestId("project-module-placeholder")).toBeNull();
    const nav = screen.getByRole("navigation", { name: "Project modules" });
    const shotBoard = within(nav).getByRole("link", { name: "Shot Board" });
    const queue = within(nav).getByRole("link", { name: "Generation Queue" });
    expect(shotBoard).toHaveAttribute("aria-current", "page");
    expect(shotBoard).toHaveAttribute("href", "/projects/project-a/shot-board");
    expect(queue).not.toHaveAttribute("aria-current");
    expect(queue).toHaveAttribute("href", "/projects/project-a/queue-monitor");
  });

  it("renders the Script Studio module with project navigation marking the active module", () => {
    mocks.params = { projectId: "project-a", module: "script-studio" };
    render(<ProjectModuleRoute />);

    expect(screen.getByTestId("script-studio-stub").textContent).toBe("project-a");
    expect(screen.queryByTestId("project-module-placeholder")).toBeNull();
    const nav = screen.getByRole("navigation", { name: "Project modules" });
    const scriptStudio = within(nav).getByRole("link", { name: "Script & Story Bible" });
    const shotBoard = within(nav).getByRole("link", { name: "Shot Board" });
    expect(scriptStudio).toHaveAttribute("aria-current", "page");
    expect(scriptStudio).toHaveAttribute("href", "/projects/project-a/script-studio");
    expect(shotBoard).not.toHaveAttribute("aria-current");
  });

  it("orders the production steps and keeps workspace tools out of project navigation", () => {
    mocks.params = { projectId: "project-a", module: "shot-board" };
    render(<ProjectModuleRoute />);

    const nav = screen.getByRole("navigation", { name: "Project modules" });
    const steps = within(within(nav).getByRole("list", { name: "Production steps" })).getAllByRole(
      "link",
    );
    expect(steps.map((link) => link.textContent)).toEqual([
      "1Script & Story Bible",
      "2Shot Board",
      "3Generation Queue",
      "4Timeline & Export",
    ]);
    const more = within(nav).getByRole("list", { name: "More project tools" });
    expect(within(more).getByRole("link", { name: /Workflow Automation/ })).toHaveTextContent(
      "Advanced",
    );
    expect(within(more).getByRole("link", { name: "Project Settings" })).toHaveAttribute(
      "href",
      "/projects/project-a/settings",
    );
    expect(within(nav).queryByRole("link", { name: /Connections/ })).toBeNull();
    expect(within(nav).queryByRole("link", { name: /System/ })).toBeNull();
  });

  it("renders the project name in a breadcrumb back to the workspace project list", () => {
    mocks.projects = {
      isPending: false,
      isSuccess: true,
      data: [{ id: "project-a", name: "Neon Horizon" } as { id: string }],
    };
    render(<ProjectModuleRoute />);

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByRole("link", { name: "Projects" })).toHaveAttribute(
      "href",
      "/projects",
    );
    expect(within(breadcrumb).getByText("Neon Horizon")).toHaveAttribute("aria-current", "page");
  });

  it("composes project settings with the project's backups and assets", () => {
    mocks.params = { projectId: "project-a", module: "settings" };
    render(<ProjectModuleRoute />);

    expect(screen.getByTestId("project-settings-stub").textContent).toBe("project-a");
    expect(screen.getByTestId("project-data-stub").textContent).toBe("project-a");
    const nav = screen.getByRole("navigation", { name: "Project modules" });
    expect(within(nav).getByRole("link", { name: "Project Settings" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders the Workflow Automation and Timeline & Export modules", () => {
    for (const [module, testId] of [
      ["workflow-canvas", "workflow-canvas-stub"],
      ["timeline-editor", "timeline-editor-stub"],
    ] as const) {
      mocks.params = { projectId: "project-a", module };
      const { unmount } = render(<ProjectModuleRoute />);
      expect(screen.getByTestId(testId).textContent).toBe("project-a");
      unmount();
    }
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
