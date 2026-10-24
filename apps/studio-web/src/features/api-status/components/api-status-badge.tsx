import { CheckCircle2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiStatusPresentation } from "@/features/api-status/status";
import { useApiStatus } from "@/features/api-status/use-api-status";

export function ApiStatusBadge() {
  const { t } = useTranslation("api-status");
  const status = useApiStatus();
  const presentation = getApiStatusPresentation(status);
  const translatedLabel = t(`states.${status}`, { defaultValue: presentation.label });

  return (
    <div className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-xs">
      {status === "online" ? (
        <CheckCircle2 className="size-4 text-success" aria-hidden="true" />
      ) : (
        <span
          className={cn("size-2 rounded-full", presentation.indicatorClassName)}
          aria-hidden="true"
        />
      )}
      <span className="text-muted-foreground">{t("badgeLabel", { defaultValue: "API" })}</span>
      <span className={presentation.textClassName} aria-live="polite">
        {translatedLabel}
      </span>
    </div>
  );
}
