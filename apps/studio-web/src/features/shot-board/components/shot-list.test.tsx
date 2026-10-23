import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotList } from "@/features/shot-board/components/shot-list";
import type { ProjectShot } from "@/features/shot-board/state";
import { makeShot } from "@/features/shot-board/test-fixtures";

afterEach(cleanup);

function renderList(shots: ProjectShot[], overrides: Partial<Parameters<typeof ShotList>[0]> = {}) {
  return render(
    <ShotList
      shots={shots}
      editingShotId={null}
      onEdit={vi.fn()}
      onGenerate={vi.fn()}
      reviewingShotId={null}
      onReviewTakes={vi.fn()}
      canGenerate
      generateDisabledReason={null}
      renderShotDetail={() => null}
      {...overrides}
    />,
  );
}

const bareShot = makeShot({
  id: "shot-1",
  title: "Bare",
  location_code: "",
  camera_motion: "",
  shot_size: "",
  dialogue: "",
  action: "Hero waits.",
  duration_seconds: 0.1,
});

describe("ShotList", () => {
  it("shows a dash instead of invented values when shot data is missing", () => {
    renderList([bareShot, makeShot({ ...bareShot, id: "shot-2", duration_seconds: 0.2 })]);
    const scene = screen.getByTestId("shot-scene-scene-1");

    for (const fabricated of [
      "Main Stage",
      "Cinematic",
      "Visual sequence beat",
      "Ambient track",
      "medium",
      "static",
      "Lighting",
      "Voiceover",
      "Motion Scale",
      "⋮⋮",
    ]) {
      expect(scene).not.toHaveTextContent(fabricated);
    }
    // Dialogue must not fall back to the action text.
    const sidebar = within(scene).getByText("Dialogue (first shot with dialogue)").parentElement;
    expect(sidebar).toHaveTextContent("—");
    expect(sidebar).not.toHaveTextContent("Hero waits.");
    // The header and the details list agree on missing framing.
    const card = screen.getByTestId("shot-card-shot-1");
    expect(within(card).getAllByText(/— · —/)).toHaveLength(2);
    // Float sums are rounded (0.1 + 0.2).
    expect(scene).toHaveTextContent("0.3s");
    expect(scene).not.toHaveTextContent("0.30000000000000004");
  });

  it("keeps action buttons wrappable and their accessible names stable", () => {
    renderList([makeShot({ id: "shot-2", sequence_index: 2, title: "Chase" })]);
    const card = screen.getByTestId("shot-card-shot-2");
    const edit = within(card).getByRole("button", { name: "Edit Shot 2 · Chase" });
    within(card).getByRole("button", { name: "Generate Shot 2 · Chase" });
    within(card).getByRole("button", { name: "Review takes for Shot 2 · Chase" });
    expect(edit.parentElement).toHaveClass("flex", "flex-wrap");
    expect(edit.parentElement).not.toHaveClass("grid-cols-3");
  });

  it("spans the full row while a generate confirm or enqueue result is open", () => {
    renderList(
      [makeShot({ id: "shot-1" }), makeShot({ id: "shot-2", sequence_index: 2, title: "Chase" })],
      { expandedShotIds: new Set(["shot-2"]) },
    );
    expect(screen.getByTestId("shot-card-shot-2")).toHaveClass("md:col-span-2");
    expect(screen.getByTestId("shot-card-shot-1")).not.toHaveClass("md:col-span-2");
  });
});
