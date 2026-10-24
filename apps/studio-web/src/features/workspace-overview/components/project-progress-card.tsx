import { Link } from "@tanstack/react-router";
import { AlertCircle, ArrowRight, LoaderCircle, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import type { Project } from "@/entities/project/api";
import {
  nextStepCopy,
  type NextStep,
  type ProjectProgress,
} from "@/features/workspace-overview/progress";
import { formatDateTime } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

type ProgressState =
  | { status: "loading" }
  | { status: "error"; message: string; onRetry: () => void }
  | { status: "ready"; progress: ProjectProgress; nextStep: NextStep };

interface ProjectProgressCardProps {
  project: Project;
  state: ProgressState;
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warn" | "bad" }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-[11px] text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "text-sm font-semibold tabular-nums",
          value > 0 && tone === "bad" && "text-destructive",
          value > 0 && tone === "warn" && "text-warning",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function NextStepLink({ projectId, step }: { projectId: string; step: NextStep }) {
  const copy = nextStepCopy(step);
  const className =
    "inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground shadow-xs transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const content = (
    <>
      {copy.action}
      <ArrowRight className="size-3.5" aria-hidden="true" />
    </>
  );
  // Shot Board steps deep-link to the exact shot (and tab) that needs attention.
  if (step.kind === "resolve_blocked" || step.kind === "pick_takes") {
    return (
      <Link
        to="/projects/$projectId/$module"
        params={{ projectId, module: step.module }}
        search={{ shot: step.shotId, tab: step.kind === "resolve_blocked" ? step.tab : undefined }}
        className={className}
      >
        {content}
      </Link>
    );
  }
  return (
    <Link
      to="/projects/$projectId/$module"
      params={{ projectId, module: step.module }}
      className={className}
    >
      {content}
    </Link>
  );
}

/** One recent project: shot progress from `GET /projects/{id}/shots` and the next action. */
export function ProjectProgressCard({ project, state }: ProjectProgressCardProps) {
  const { t } = useTranslation("workspace-overview");

  return (
    <li
      aria-label={project.name}
      className="flex min-w-0 flex-col gap-4 rounded-xl border border-border bg-card p-4 shadow-2xs"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to="/projects/$projectId/$module"
            params={{ projectId: project.id, module: "shot-board" }}
            className="block truncate text-base font-semibold hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {project.name}
          </Link>
          <p className="text-xs text-muted-foreground">
            {t("updatedAt", { time: formatDateTime(project.updated_at) })}
          </p>
        </div>
        <span className="shrink-0 rounded bg-secondary px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
          {project.aspect_ratio} · {project.fps} fps
        </span>
      </div>

      {state.status === "loading" && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          {t("loadingProjects", { defaultValue: "Checking progress..." })}
        </p>
      )}

      {state.status === "error" && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <p className="flex items-start gap-2 text-muted-foreground">
            <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
            {state.message}
          </p>
          <Button
            variant="outline"
            size="sm"
            aria-label={`Retry loading progress for ${project.name}`}
            onClick={state.onRetry}
          >
            <RefreshCw aria-hidden="true" />
            {t("common:actions.retry", { defaultValue: "Retry" })}
          </Button>
        </div>
      )}

      {state.status === "ready" && (
        <>
          {state.progress.total > 0 && (
            <div className="space-y-2">
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="text-muted-foreground">
                  {t("shotsProgress", {
                    withTake: state.progress.withTake,
                    total: state.progress.total,
                  })}
                </span>
                <span className="font-semibold tabular-nums">
                  {state.progress.withTake} / {state.progress.total}
                </span>
              </div>
              <div
                role="progressbar"
                aria-label={t("shotsProgress", {
                  withTake: state.progress.withTake,
                  total: state.progress.total,
                })}
                aria-valuemin={0}
                aria-valuemax={state.progress.total}
                aria-valuenow={state.progress.withTake}
                className="h-1.5 overflow-hidden rounded-full bg-secondary"
              >
                <div
                  className="h-full rounded-full bg-primary"
                  style={{ width: `${(state.progress.withTake / state.progress.total) * 100}%` }}
                />
              </div>
              <dl className="grid grid-cols-4 gap-2">
                <Stat
                  label={t("stats.labels.blocked", { defaultValue: "Blocked" })}
                  value={state.progress.blocked}
                  tone="bad"
                />
                <Stat
                  label={t("stats.labels.failed", { defaultValue: "Failed" })}
                  value={state.progress.failed}
                  tone="bad"
                />
                <Stat
                  label={t("stats.labels.generating", { defaultValue: "Generating" })}
                  value={state.progress.inFlight}
                />
                <Stat
                  label={t("stats.labels.approved", { defaultValue: "Approved" })}
                  value={state.progress.approved}
                />
              </dl>
            </div>
          )}

          <div
            className={cn(
              "flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between",
              state.nextStep.kind === "resolve_blocked" || state.nextStep.kind === "check_failed"
                ? "border-destructive/25 bg-destructive/5"
                : "border-primary/20 bg-primary/5",
            )}
          >
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {t("nextSteps.title", { defaultValue: "Next step" })}
              </p>
              <p className="text-sm font-semibold">{nextStepCopy(state.nextStep).title}</p>
              <p className="text-xs text-muted-foreground">{nextStepCopy(state.nextStep).detail}</p>
            </div>
            <div className="shrink-0">
              <NextStepLink projectId={project.id} step={state.nextStep} />
            </div>
          </div>
        </>
      )}
    </li>
  );
}

export type { ProgressState };
