import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

import { BatchLaunchPanel } from "@/features/shot-board/components/batch-launch-panel";
import { makeBatchResult, makeConnection, makeShot } from "@/features/shot-board/test-fixtures";

afterEach(() => cleanup());

const shots = [
  makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters" }),
  makeShot({
    id: "shot-2",
    sequence_index: 2,
    title: "Chase",
    continuity_mode: "frame_chain",
    previous_shot_id: "shot-1",
    lint_report: [
      { code: "MISSING_REF", severity: "blocker", message: "Reference missing", field: null },
    ],
  }),
  makeShot({ id: "shot-3", scene_id: "scene-2", sequence_index: 3, title: "Rooftop" }),
];

function renderPanel(overrides: Partial<Parameters<typeof BatchLaunchPanel>[0]> = {}) {
  const onLaunch = vi.fn();
  render(
    <BatchLaunchPanel
      projectId="project-1"
      shots={shots}
      sceneIds={["scene-1", "scene-2"]}
      connection={makeConnection({ provider_type: "muse2api", name: "Live gateway" })}
      modelId="muse-video-v1"
      taskType="video"
      isLaunching={false}
      result={null}
      errorMessage={null}
      onLaunch={onLaunch}
      {...overrides}
    />,
  );
  return { onLaunch };
}

describe("BatchLaunchPanel", () => {
  it("confirms provider, model and shot count before launching a scene batch", async () => {
    const user = userEvent.setup();
    const { onLaunch } = renderPanel();

    await user.selectOptions(screen.getByLabelText("Scope"), "scene-1");
    await user.click(screen.getByRole("button", { name: "Launch batch" }));

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Launch batch for 2 shots?");
    expect(dialog).toHaveTextContent("Live gateway");
    expect(dialog).toHaveTextContent("Live provider (muse2api): may consume quota");
    expect(dialog).toHaveTextContent("muse-video-v1");
    expect(onLaunch).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Confirm launch" }));
    expect(onLaunch).toHaveBeenCalledWith({
      connection_id: "conn-mock",
      model_id: "muse-video-v1",
      only_changed: true,
      scene_id: "scene-1",
    });
  });

  it("requires a separate acknowledgement before running all shots regardless of changes", async () => {
    const user = userEvent.setup();
    const { onLaunch } = renderPanel();

    await user.click(screen.getByLabelText(/Run all regardless of changes/));
    await user.click(screen.getByRole("button", { name: "Launch batch" }));
    const confirm = screen.getByRole("button", { name: "Confirm launch" });
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      "All 3 shots in scope will be rendered again",
    );

    await user.click(screen.getByLabelText(/all 3 shots will be rerun/));
    await user.click(confirm);
    expect(onLaunch).toHaveBeenCalledWith(
      expect.objectContaining({ only_changed: false, model_id: "muse-video-v1" }),
    );
    expect(onLaunch.mock.calls[0]?.[0]).not.toHaveProperty("scene_id");
  });

  it("labels the mock provider as free and refuses to launch with an image output", () => {
    renderPanel({ connection: makeConnection(), taskType: "image" });
    expect(screen.getByRole("button", { name: "Launch batch" })).toBeDisabled();
    expect(screen.getByText(/Batch launch renders video/)).toBeInTheDocument();
  });

  it("renders frame-chain warnings with affected shots and blocked shots with reasons", () => {
    renderPanel({
      result: makeBatchResult({
        warnings: [
          {
            code: "frame_chain_continuity_not_guaranteed",
            message: "Connection 'Live gateway' first-frame conditioning is 'unknown'.",
            shot_ids: ["shot-2", "shot-unknown"],
          },
        ],
      }),
    });

    const result = screen.getByTestId("batch-launch-result");
    expect(within(result).getByRole("status")).toHaveTextContent(
      "Batch bat_0001: 1 queued, 1 skipped, 1 blocked of 3 shots.",
    );
    const warning = within(result).getByTestId("batch-warning");
    expect(warning).toHaveAttribute("data-code", "frame_chain_continuity_not_guaranteed");
    expect(warning).toHaveTextContent("Frame-chain continuity not guaranteed");
    expect(warning).toHaveTextContent("first-frame conditioning is 'unknown'");
    expect(
      within(warning)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["Shot 2 · Chase", "shot-unknown"]);
    expect(within(result).getByTestId("batch-blocked-shots")).toHaveTextContent(
      "Shot 2 · Chase: Prompt Linter blocker MISSING_REF: Reference missing",
    );
    expect(result).toHaveTextContent(
      "Skipped (unchanged or an existing task already covers them): Shot 3 · Rooftop",
    );
    expect(within(result).getByText("Follow progress in Queue Monitor")).toBeInTheDocument();
  });
});
