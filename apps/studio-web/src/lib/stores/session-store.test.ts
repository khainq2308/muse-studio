import { afterEach, describe, expect, it } from "vitest";
import { useSessionStore } from "./session-store";

afterEach(() => {
  useSessionStore.getState().clearAdminToken();
});

describe("admin session state", () => {
  it("keeps the secret in memory and uses only a non-secret cache version", () => {
    const initialVersion = useSessionStore.getState().sessionVersion;

    useSessionStore.getState().setAdminToken("super-secret");
    expect(useSessionStore.getState().adminToken).toBe("super-secret");
    expect(useSessionStore.getState().sessionVersion).toBe(initialVersion + 1);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("clears the secret and advances the cache version on disconnect", () => {
    useSessionStore.getState().setAdminToken("super-secret");
    const versionBeforeClear = useSessionStore.getState().sessionVersion;

    useSessionStore.getState().clearAdminToken();

    expect(useSessionStore.getState().adminToken).toBe("");
    expect(useSessionStore.getState().sessionVersion).toBe(versionBeforeClear + 1);
  });
});
