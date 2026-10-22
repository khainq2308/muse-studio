import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AdminTokenForm } from "@/features/admin-session/components/admin-token-form";
import { useSessionStore } from "@/lib/stores/session-store";

afterEach(() => {
  cleanup();
  act(() => useSessionStore.getState().clearAdminToken());
});

describe("AdminTokenForm", () => {
  it("connects with a token and clears the password field", () => {
    render(<AdminTokenForm />);
    const input = screen.getByLabelText("Studio admin token");

    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "  admin-token-1  " } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    expect(useSessionStore.getState().adminToken).toBe("admin-token-1");
    expect(input).toHaveValue("");
  });

  it("does not silently disconnect when an empty form is submitted while connected", () => {
    act(() => useSessionStore.getState().setAdminToken("admin-token-1"));
    render(<AdminTokenForm />);
    const input = screen.getByLabelText("Studio admin token");

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form")!);
    expect(useSessionStore.getState().adminToken).toBe("admin-token-1");
    expect(screen.getByRole("button", { name: "Update token" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(useSessionStore.getState().adminToken).toBe("");
  });
});
