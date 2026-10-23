import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Clapperboard,
  GitBranch,
  LayoutGrid,
  ListChecks,
  Sparkles,
} from "lucide-react";
import { useConnections } from "@/entities/connection/api";
import { useProjects } from "@/entities/project/api";
import { ConnectionList } from "@/features/workspace-overview/components/connection-list";
import { ProjectList } from "@/features/workspace-overview/components/project-list";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

const PIPELINE_STEPS = [
  {
    step: "01",
    title: "Script & Story Bible",
    module: "script-studio",
    description:
      "Parse MuseFlow/Markdown scripts, lock character reference sheets & compile prompts.",
    icon: BookOpenText,
  },
  {
    step: "02",
    title: "Shot Board & Takes",
    module: "shot-board",
    description:
      "Review continuity lint, frame-chain boundary frames, batch launch & A/B compare takes.",
    icon: LayoutGrid,
  },
  {
    step: "03",
    title: "Workflow DAG Canvas",
    module: "workflow-canvas",
    description:
      "Inspect typed pipeline nodes, run Merkle-cached dry-runs or step through manual gates.",
    icon: GitBranch,
  },
  {
    step: "04",
    title: "Queue & Operator Reply",
    module: "queue-monitor",
    description:
      "Monitor live generation tasks, answer awaiting-input questions & tick batch scheduler.",
    icon: ListChecks,
  },
  {
    step: "05",
    title: "Timeline & MP4 Export",
    module: "timeline-editor",
    description:
      "Assemble selected takes, trim clips, attach SRT/VTT subtitles & run verified FFmpeg export.",
    icon: Clapperboard,
  },
] as const;

export function OverviewPage() {
  const projectQuery = useProjects();
  const connectionQuery = useConnections();
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const primaryProject = projectQuery.data?.[0];

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-border bg-card p-5 shadow-xs sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-1.5">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-primary/20 bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              <Sparkles className="size-3.5" aria-hidden="true" />
              End-to-End AI Video Production Pipeline
            </div>
            <h2 className="text-lg font-semibold tracking-tight sm:text-xl">
              Studio Command Center
            </h2>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Manage production workspaces, verify provider gateway health, and move from script
              breakdown to Story Bible reference locks, frame-chained shot generation, and
              FFprobe-verified timeline export.
            </p>
          </div>

          {primaryProject && (
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId: primaryProject.id, module: "shot-board" }}
                className="inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-3.5 text-xs font-semibold text-primary-foreground shadow-xs transition-opacity hover:opacity-90 sm:text-sm"
              >
                <LayoutGrid className="size-4" aria-hidden="true" />
                Open Shot Board ({primaryProject.name})
              </Link>
              <Link
                to="/projects/$projectId/$module"
                params={{ projectId: primaryProject.id, module: "script-studio" }}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-background px-3.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary sm:text-sm"
              >
                <BookOpenText className="size-4 text-muted-foreground" aria-hidden="true" />
                Script Studio
              </Link>
            </div>
          )}
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {PIPELINE_STEPS.map(({ step, title, module, description, icon: Icon }) => (
            <div
              key={step}
              className="flex flex-col justify-between rounded-xl border border-border bg-secondary/35 p-3.5"
            >
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-primary">
                    STAGE {step}
                  </span>
                  <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                </div>
                <p className="mt-2 text-xs font-semibold text-foreground">{title}</p>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p>
              </div>
              {primaryProject && (
                <Link
                  to="/projects/$projectId/$module"
                  params={{ projectId: primaryProject.id, module }}
                  className="mt-3 inline-flex items-center text-xs font-medium text-primary hover:underline"
                >
                  Open {title.split(" ")[0]} →
                </Link>
              )}
            </div>
          ))}
        </div>
      </div>

      <section aria-label="Workspace overview" className="grid gap-5 lg:grid-cols-2">
        <ProjectList
          projects={projectQuery.data ?? []}
          isLoading={projectQuery.isPending}
          isError={projectQuery.isError}
          errorMessage={authErrorMessage(projectQuery.error, activeAdminToken, "projects")}
          onRetry={() => void projectQuery.refetch()}
        />
        <ConnectionList
          connections={connectionQuery.data ?? []}
          isLoading={connectionQuery.isPending}
          isError={connectionQuery.isError}
          errorMessage={authErrorMessage(
            connectionQuery.error,
            activeAdminToken,
            "provider connections",
          )}
          onRetry={() => void connectionQuery.refetch()}
        />
      </section>
    </div>
  );
}

export default OverviewPage;
