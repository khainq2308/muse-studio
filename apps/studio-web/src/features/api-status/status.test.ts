import { describe, expect, it } from "vitest";
import {
  getApiStatusPresentation,
  resolveApiStatus,
  type ApiHealthSnapshot,
  type ApiStatus,
} from "@/features/api-status/status";

describe("API health status", () => {
  it.each([
    [{ isPending: true, isError: false, healthStatus: undefined }, "checking"],
    [{ isPending: false, isError: false, healthStatus: "ok" }, "online"],
    [{ isPending: false, isError: true, healthStatus: undefined }, "offline"],
    [{ isPending: false, isError: false, healthStatus: "degraded" }, "unknown"],
  ] satisfies Array<[ApiHealthSnapshot, ApiStatus]>)(
    "resolves a health snapshot to %s",
    (snapshot, expected) => {
      expect(resolveApiStatus(snapshot)).toBe(expected);
    },
  );

  it("maps semantic states to independent labels and theme-token classes", () => {
    expect(getApiStatusPresentation("online")).toEqual({
      label: "Online",
      textClassName: "text-success",
      indicatorClassName: "bg-success",
    });
    expect(getApiStatusPresentation("offline").label).toBe("Offline (start Orchestrator)");
    expect(getApiStatusPresentation("checking").textClassName).toBe("text-muted-foreground");
    expect(getApiStatusPresentation("unknown").textClassName).toBe("text-warning");
  });
});
