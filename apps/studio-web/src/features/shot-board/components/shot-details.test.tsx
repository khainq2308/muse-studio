import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ShotDetails, ShotPromptLint } from "@/features/shot-board/components/shot-details";
import { makeShot } from "@/features/shot-board/test-fixtures";

afterEach(cleanup);

const bareShot = makeShot({
  id: "shot-1",
  title: "Bare",
  subject_codes: [],
  location_code: "",
  camera_motion: "",
  shot_size: "",
  composition: "",
  dialogue: "",
  action: "Hero waits.",
  prompt_final: "",
  prompt_hash: "",
  duration_seconds: 0.1 + 0.2,
});

function field(container: HTMLElement, label: string) {
  return within(container).getByText(label).nextElementSibling as HTMLElement;
}

describe("ShotDetails", () => {
  it("shows a dash instead of invented values when shot data is missing", () => {
    render(<ShotDetails shot={bareShot} shots={[bareShot]} />);
    const details = screen.getByTestId("shot-details");

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
    ]) {
      expect(details).not.toHaveTextContent(fabricated);
    }
    for (const label of [
      "Location",
      "Shot size",
      "Camera motion",
      "Composition",
      "Previous shot",
      "Next shot",
      "First frame asset",
      "Last frame asset",
      "Selected take",
      "Reference assets",
    ]) {
      expect(field(details, label)).toHaveTextContent(/^—$/);
    }
    // Dialogue must not fall back to the action text.
    expect(screen.getByTestId("shot-dialogue")).toHaveTextContent(/^—$/);
    expect(field(details, "Action & subjects")).toHaveTextContent("Hero waits.");
    // Float durations are rounded (0.1 + 0.2).
    expect(field(details, "Duration")).toHaveTextContent(/^0\.3s$/);
  });

  it("names linked shots and shows stored asset IDs", () => {
    const previous = makeShot({ id: "shot-0", sequence_index: 0, title: "Prologue" });
    const shot = makeShot({
      previous_shot_id: "shot-0",
      next_shot_id: "shot-gone",
      first_frame_asset_id: "asset-ff",
      selected_take_id: "take-7",
      continuity_mode: "frame_chain",
    });
    render(<ShotDetails shot={shot} shots={[previous, shot]} />);
    const details = screen.getByTestId("shot-details");
    expect(field(details, "Previous shot")).toHaveTextContent("Shot 0 · Prologue");
    expect(field(details, "Next shot")).toHaveTextContent("shot-gone");
    expect(screen.getByTestId("shot-first-frame")).toHaveTextContent("asset-ff");
    expect(field(details, "Selected take")).toHaveTextContent("take-7");
    expect(field(details, "Continuity")).toHaveTextContent("Frame chain");
  });
});

describe("ShotPromptLint", () => {
  it("shows the compiled prompt read-only with the lint report, and a dash when empty", () => {
    const shot = makeShot({
      lint_report: [
        { code: "MISSING_REFERENCE", severity: "blocker", message: "Need ref.", field: null },
      ],
    });
    render(<ShotPromptLint shot={shot} />);
    expect(screen.getByTestId("shot-prompt-final")).toHaveTextContent(
      "wide static shot of the hero walking into the hall",
    );
    expect(screen.getByTestId("shot-prompt-final").tagName).toBe("P");
    expect(screen.getByTestId("shot-negative-prompt")).toHaveTextContent(/^—$/);
    expect(
      within(
        screen.getByRole("list", { name: "Prompt Linter for Shot 1 · Hero enters" }),
      ).getByText("Need ref."),
    ).toBeInTheDocument();
    cleanup();

    render(<ShotPromptLint shot={bareShot} />);
    expect(screen.getByTestId("shot-prompt-final")).toHaveTextContent(/^—$/);
    expect(screen.getByText("Prompt Linter: no issues")).toBeInTheDocument();
  });
});
