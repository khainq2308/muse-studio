import { AlertTriangle, CircleCheck, Info, OctagonAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ShotLintIssue } from "@/features/shot-board/state";
import { cn } from "@/lib/utils";

const SEVERITY_LABELS: Record<ShotLintIssue["severity"], string> = {
  blocker: "Blocker",
  warning: "Warning",
  info: "Info",
};

const SEVERITY_TONES: Record<ShotLintIssue["severity"], string> = {
  blocker: "border-destructive/30 bg-destructive/5 text-destructive",
  warning: "border-warning/30 bg-warning/5 text-warning",
  info: "border-border bg-muted/40 text-muted-foreground",
};

interface ShotLintReportProps {
  issues: readonly ShotLintIssue[];
  /** Accessible name of the list, e.g. "Prompt Linter for Shot 3". */
  label: string;
}

/** The backend Prompt Linter report (`lint_report`), rendered as plain text. */
export function ShotLintReport({ issues, label }: ShotLintReportProps) {
  const { t } = useTranslation("shot-board");
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <CircleCheck className="size-4 text-success" aria-hidden="true" />
        {t("lint.noIssues")}
      </p>
    );
  }

  return (
    <ul aria-label={label} className="space-y-2">
      {issues.map((issue, index) => {
        const severity = Object.prototype.hasOwnProperty.call(SEVERITY_LABELS, issue.severity)
          ? issue.severity
          : "info";
        const Icon =
          severity === "blocker" ? OctagonAlert : severity === "warning" ? AlertTriangle : Info;
        return (
          <li
            key={`${issue.code}:${issue.field ?? ""}:${index}`}
            data-severity={severity}
            className={cn(
              "flex items-start gap-2 rounded-md border p-2 text-xs",
              SEVERITY_TONES[severity],
            )}
          >
            <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span className="min-w-0">
              <span className="font-semibold">{SEVERITY_LABELS[severity]}</span>{" "}
              <code className="break-all">{issue.code}</code>
              {issue.field && <span className="text-muted-foreground"> ({issue.field})</span>}:{" "}
              <span className="break-words text-foreground">{issue.message}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
