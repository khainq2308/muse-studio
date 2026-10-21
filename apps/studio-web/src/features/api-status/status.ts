export type ApiStatus = "checking" | "online" | "offline" | "unknown";

export interface ApiHealthSnapshot {
  isPending: boolean;
  isError: boolean;
  healthStatus: string | undefined;
}

export interface ApiStatusPresentation {
  label: string;
  textClassName: string;
  indicatorClassName: string;
}

export const API_STATUS_PRESENTATION = {
  checking: {
    label: "Checking...",
    textClassName: "text-muted-foreground",
    indicatorClassName: "bg-muted-foreground",
  },
  online: {
    label: "Online",
    textClassName: "text-success",
    indicatorClassName: "bg-success",
  },
  offline: {
    label: "Offline (start Orchestrator)",
    textClassName: "text-destructive",
    indicatorClassName: "bg-destructive",
  },
  unknown: {
    label: "Unknown",
    textClassName: "text-warning",
    indicatorClassName: "bg-warning",
  },
} satisfies Record<ApiStatus, ApiStatusPresentation>;

export function resolveApiStatus(snapshot: ApiHealthSnapshot): ApiStatus {
  if (snapshot.isPending) return "checking";
  if (snapshot.isError) return "offline";
  if (snapshot.healthStatus === "ok") return "online";
  return "unknown";
}

export function getApiStatusPresentation(status: ApiStatus): ApiStatusPresentation {
  return API_STATUS_PRESENTATION[status];
}
