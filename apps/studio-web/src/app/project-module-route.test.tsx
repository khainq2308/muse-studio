import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  params: { projectId: "project-a", module: "queue-monitor" },
  mounts: [] as string[],
}));

vi.mock("@tanstack/react-router", () => ({
  useParams: () => mocks.params,
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

afterEach(() => cleanup());

describe("ProjectModuleRoute", () => {
  it("remounts the queue monitor when the project changes", () => {
    const { rerender, getByTestId } = render(<ProjectModuleRoute />);
    mocks.params = { projectId: "project-b", module: "queue-monitor" };
    rerender(<ProjectModuleRoute />);

    expect(getByTestId("queue-stub").textContent).toBe("project-b");
    expect(mocks.mounts).toEqual(["project-a", "project-b"]);
  });
});
