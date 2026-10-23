import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotEditPanel } from "@/features/shot-board/components/shot-edit-panel";
import { makeReview, makeShot } from "@/features/shot-board/test-fixtures";

afterEach(() => cleanup());

const shot = makeShot({ id: "shot-2", sequence_index: 2, title: "Chase", previous_shot_id: null });
const siblings = [
  makeShot({ id: "shot-1", sequence_index: 1, title: "Hero enters" }),
  shot,
  makeShot({ id: "other-scene", scene_id: "scene-9", sequence_index: 3, title: "Elsewhere" }),
];

function renderPanel(overrides: Partial<Parameters<typeof ShotEditPanel>[0]> = {}) {
  const onSave = vi.fn();
  const onClose = vi.fn();
  render(
    <ShotEditPanel
      shot={shot}
      shots={siblings}
      isSaving={false}
      fieldErrors={{}}
      formError={null}
      review={null}
      onSave={onSave}
      onClose={onClose}
      {...overrides}
    />,
  );
  return { onSave, onClose };
}

describe("ShotEditPanel", () => {
  it("submits only the changed fields, including a previous shot from the same scene", async () => {
    const user = userEvent.setup();
    const { onSave } = renderPanel();

    const duration = screen.getByLabelText("Duration (seconds)");
    await user.clear(duration);
    await user.type(duration, "6.5");
    await user.selectOptions(screen.getByLabelText("Continuity mode"), "frame_chain");
    const previous = screen.getByLabelText("Previous shot (same scene)");
    expect(
      Array.from((previous as HTMLSelectElement).options).map((option) => option.value),
    ).toEqual(["", "shot-1"]);
    await user.selectOptions(previous, "shot-1");
    await user.click(screen.getByRole("button", { name: "Save shot" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      duration_seconds: 6.5,
      continuity_mode: "frame_chain",
      previous_shot_id: "shot-1",
    });
  });

  it("blocks invalid values on the client before calling the API", async () => {
    const user = userEvent.setup();
    const { onSave } = renderPanel();

    await user.clear(screen.getByLabelText("Action"));
    const duration = screen.getByLabelText("Duration (seconds)");
    await user.clear(duration);
    await user.type(duration, "601");
    await user.click(screen.getByRole("button", { name: "Save shot" }));

    expect(await screen.findByText("Action must not be blank.")).toBeInTheDocument();
    expect(screen.getByText("Duration must be at most 600 seconds.")).toBeInTheDocument();
    expect(screen.getByLabelText("Action")).toHaveAttribute("aria-invalid", "true");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("shows API field errors inline and other errors above the form", async () => {
    renderPanel({
      fieldErrors: { duration_seconds: "Input should be less than or equal to 600" },
      formError: "The shot was not saved: reference_asset_ids: Too many items",
    });

    expect(
      await screen.findByText("Input should be less than or equal to 600"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Duration (seconds)")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("reference_asset_ids: Too many items");
  });

  it("shows the recompiled prompt as read-only text with the new lint report", () => {
    renderPanel({
      review: makeReview({
        approval_state: "needs_review",
        lint_report: [
          { code: "MISSING_REF", severity: "blocker", message: "Reference missing", field: null },
        ],
      }),
    });

    expect(
      screen.getByText("Final prompt (compiled from the shot fields, read-only)"),
    ).toBeVisible();
    const prompt = screen.getByTestId("shot-review-prompt");
    expect(prompt.tagName).toBe("P");
    expect(prompt).toHaveTextContent("close-up handheld shot of the hero running");
    expect(screen.getByRole("status")).toHaveTextContent("Approval: Needs review");
    expect(screen.getByRole("list", { name: "Prompt Linter after save" })).toHaveTextContent(
      "MISSING_REF",
    );
  });

  it("warns that changing continuity clears an existing first frame", async () => {
    const user = userEvent.setup();
    render(
      <ShotEditPanel
        shot={{ ...shot, first_frame_asset_id: "asset-ff" }}
        shots={siblings}
        isSaving={false}
        fieldErrors={{}}
        formError={null}
        review={null}
        onSave={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByRole("note")).toBeNull();
    await user.selectOptions(screen.getByLabelText("Continuity mode"), "frame_chain");
    expect(screen.getByRole("note")).toHaveTextContent("clears this shot's first-frame asset");
  });
});
