import { CheckCircle2 } from "lucide-react";
import { getApiStatusPresentation } from "@/features/api-status/status";
import { useApiStatus } from "@/features/api-status/use-api-status";

export function ApiStatusBadge() {
  const status = useApiStatus();
  const presentation = getApiStatusPresentation(status);

  return (
    <div className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-xs">
      {status === "online" ? (
        <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
      ) : (
        <span
          className={`size-2 rounded-full ${presentation.indicatorClassName}`}
          aria-hidden="true"
        />
      )}
      <span className="text-muted-foreground">API</span>
      <span className={presentation.textClassName} aria-live="polite">
        {presentation.label}
      </span>
    </div>
  );
}
