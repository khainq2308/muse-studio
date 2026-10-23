import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => <a href="/">{children as string}</a>,
}));

import { ContinuityFlagForm } from "@/features/shot-board/components/continuity-flag-form";
import { makeConnection, makeTake } from "@/features/shot-board/test-fixtures";

afterEach(() => cleanup());

function props(overrides: Partial<Parameters<typeof ContinuityFlagForm>[0]> = {}) {
  return {
    projectId: "project-1",
    take: makeTake(),
    shotLabel: "Shot 1 · Hero enters",
    connection: makeConnection(),
    modelId: "muse-video-mock-v1",
    regenerateDisabledReason: null,
    isSubmitting: false,
    error: null,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    onRefreshTakes: vi.fn(),
    ...overrides,
  };
}

describe("ContinuityFlagForm confirmation", () => {
  it("closes the confirmation when Generation settings stop allowing regeneration", async () => {
    const user = userEvent.setup();
    const initial = props();
    const { rerender } = render(<ContinuityFlagForm {...initial} />);

    await user.type(screen.getByLabelText("Director notes"), "Lamp is missing.");
    await user.click(screen.getByRole("checkbox", { name: "Regenerate this shot now" }));
    await user.click(screen.getByRole("button", { name: "Flag and regenerate" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    // The operator clears the connection while the confirmation is open.
    rerender(
      <ContinuityFlagForm
        {...initial}
        connection={undefined}
        regenerateDisabledReason="Select a connection in Generation settings first."
      />,
    );

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirm and regenerate" }),
    ).not.toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Flag continuity" });
    expect(submit).toBeEnabled();
    expect(submit).toHaveFocus();
    expect(screen.getByRole("checkbox", { name: "Regenerate this shot now" })).toBeDisabled();
    expect(initial.onSubmit).not.toHaveBeenCalled();

    // The form is not stuck: flagging without regeneration still works.
    await user.click(submit);
    expect(initial.onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ regenerate_now: false, director_notes: "Lamp is missing." }),
    );
  });

  it("shows the Queue Monitor link and a take refresh for an unknown outcome", async () => {
    const user = userEvent.setup();
    const initial = props({
      error: { message: "The outcome is unknown: check first.", outcomeUnknown: true },
    });
    render(<ContinuityFlagForm {...initial} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Check the Queue Monitor");
    await user.click(screen.getByRole("button", { name: "Refresh takes" }));
    expect(initial.onRefreshTakes).toHaveBeenCalledTimes(1);
  });
});
