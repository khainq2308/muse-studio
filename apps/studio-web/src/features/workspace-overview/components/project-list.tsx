import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Clapperboard,
  Copy,
  Film,
  FolderPlus,
  GitBranch,
  LayoutGrid,
  ListChecks,
  LoaderCircle,
  Sparkles,
  X,
} from "lucide-react";
import { useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { ResourceCard } from "@/components/shared/resource-card";
import { Button } from "@/components/ui/button";
import { useCreateProject, useDuplicateProject } from "@/entities/project/api";
import {
  PROJECT_ASPECT_RATIO_OPTIONS,
  PROJECT_CREATE_DEFAULTS,
  PROJECT_DUPLICATE_SCOPE_NOTE,
  PROJECT_FPS_OPTIONS,
  PROJECT_LANGUAGE_OPTIONS,
  PROJECT_OUTPUT_PROFILE_HINT,
  PROJECT_STYLE_PRESET_OPTIONS,
} from "@/entities/project/options";
import { actionErrorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

interface ProjectListProps {
  projects: components["schemas"]["ProjectOut"][];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

export function ProjectList({
  projects,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: ProjectListProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const createMutation = useCreateProject();
  const duplicateMutation = useDuplicateProject();

  const [showCreateForm, setShowCreateForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [aspectRatio, setAspectRatio] = useState<string>(PROJECT_CREATE_DEFAULTS.aspect_ratio);
  const [fps, setFps] = useState<number>(PROJECT_CREATE_DEFAULTS.fps);
  const [language, setLanguage] = useState<string>(PROJECT_CREATE_DEFAULTS.language);
  const [stylePreset, setStylePreset] = useState<string>(PROJECT_CREATE_DEFAULTS.style_preset);
  const [duplicatingId, setDuplicatingId] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<{
    kind: "created" | "duplicated";
    projectId: string;
    name: string;
  } | null>(null);

  const canSubmit = name.trim().length > 0 && !createMutation.isPending;

  return (
    <ResourceCard
      icon={<Film className="size-5" aria-hidden="true" />}
      title="Projects"
      description="Production workspaces"
      count={projects.length}
      action={
        <Button
          type="button"
          size="sm"
          variant={showCreateForm ? "outline" : "default"}
          onClick={() => {
            createMutation.reset();
            setShowCreateForm((current) => !current);
          }}
        >
          {showCreateForm ? (
            <>
              <X className="size-4" aria-hidden="true" />
              Close
            </>
          ) : (
            <>
              <FolderPlus className="size-4" aria-hidden="true" />
              New project
            </>
          )}
        </Button>
      }
    >
      {showCreateForm && (
        <form
          aria-label="Create project"
          className="mb-5 space-y-3.5 rounded-xl border border-primary/20 bg-secondary/40 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!canSubmit) return;
            setLastResult(null);
            createMutation.mutate(
              {
                name: name.trim(),
                description: description.trim(),
                aspect_ratio: aspectRatio,
                fps,
                language,
                style_preset: stylePreset,
                output_profile: PROJECT_CREATE_DEFAULTS.output_profile,
              },
              {
                onSuccess: (created) => {
                  setName("");
                  setDescription("");
                  setShowCreateForm(false);
                  setLastResult({ kind: "created", projectId: created.id, name: created.name });
                },
              },
            );
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <Sparkles className="size-4 text-primary" aria-hidden="true" />
              Create production workspace
            </h3>
            <span className="text-xs text-muted-foreground">POST /api/v1/projects</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Project name</span>
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="e.g. Episode 01 — Neon Horizon"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Production brief / description</span>
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Optional logline, visual direction, or episode notes"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Aspect ratio</span>
              <select
                value={aspectRatio}
                onChange={(event) => setAspectRatio(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_ASPECT_RATIO_OPTIONS.map((ratio) => (
                  <option key={ratio} value={ratio}>
                    {ratio}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Frame rate (FPS)</span>
              <select
                value={fps}
                onChange={(event) => setFps(Number(event.target.value))}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_FPS_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value} fps
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Style preset</span>
              <select
                value={stylePreset}
                onChange={(event) => setStylePreset(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_STYLE_PRESET_OPTIONS.map((preset) => (
                  <option key={preset} value={preset}>
                    {preset}
                  </option>
                ))}
              </select>
            </label>

            <label className="space-y-1 text-xs font-medium">
              <span>Language</span>
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_LANGUAGE_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>

            <div className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Output profile</span>
              <p className="text-xs font-normal text-muted-foreground">
                <span className="font-mono text-foreground">
                  {PROJECT_CREATE_DEFAULTS.output_profile}
                </span>{" "}
                — {PROJECT_OUTPUT_PROFILE_HINT}
              </p>
            </div>
          </div>

          {createMutation.isError && (
            <p role="alert" className="text-xs text-destructive">
              {actionErrorMessage(createMutation.error, activeAdminToken, "create project")}
            </p>
          )}

          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setShowCreateForm(false)}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={!canSubmit}>
              {createMutation.isPending ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  Creating...
                </>
              ) : (
                "Create project"
              )}
            </Button>
          </div>
        </form>
      )}

      {lastResult && (
        <p
          role="status"
          className="mb-3 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-xs text-foreground"
        >
          {lastResult.kind === "duplicated" ? (
            <>
              Duplicated project settings as <strong>{lastResult.name}</strong>{" "}
              <span className="font-mono">({lastResult.projectId})</span>.{" "}
              {PROJECT_DUPLICATE_SCOPE_NOTE}
            </>
          ) : (
            <>
              Created project <strong>{lastResult.name}</strong>{" "}
              <span className="font-mono">({lastResult.projectId})</span>.
            </>
          )}
        </p>
      )}

      {duplicateMutation.isError && (
        <p role="alert" className="mb-3 text-xs text-destructive">
          {actionErrorMessage(duplicateMutation.error, activeAdminToken, "duplicate project")}
        </p>
      )}

      <QueryState
        isLoading={isLoading}
        isError={isError}
        isEmpty={projects.length === 0}
        loadingMessage="Loading projects..."
        errorMessage={errorMessage}
        emptyTitle="No projects yet"
        emptyDescription="Create a project to begin organizing scripts, shots, and takes."
        onRetry={onRetry}
      >
        <ul className="divide-y divide-border">
          {projects.map((project) => (
            <li key={project.id} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="truncate text-sm font-semibold text-foreground">{project.name}</p>
                    <span className="rounded-md border border-border bg-secondary/70 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
                      {project.style_preset}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {project.aspect_ratio} · {project.fps} fps
                  </p>
                  {project.description && (
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {project.description}
                    </p>
                  )}
                </div>
                <div className="shrink-0 text-right text-xs text-muted-foreground">
                  <p className="font-medium text-foreground">
                    Revision {project.current_revision_num}
                  </p>
                  <p className="mt-1">{project.asset_count} assets</p>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: project.id, module: "shot-board" }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-2.5 text-xs font-medium text-primary-foreground shadow-2xs transition-opacity hover:opacity-90"
                  >
                    <LayoutGrid className="size-3.5" aria-hidden="true" />
                    Shot Board
                  </Link>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: project.id, module: "script-studio" }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
                  >
                    <BookOpenText className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    Script Studio
                  </Link>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: project.id, module: "workflow-canvas" }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
                  >
                    <GitBranch className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    Workflow
                  </Link>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: project.id, module: "queue-monitor" }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
                  >
                    <ListChecks className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    Queue
                  </Link>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: project.id, module: "timeline-editor" }}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-foreground transition-colors hover:bg-secondary"
                  >
                    <Clapperboard className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    Timeline
                  </Link>
                </div>

                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Duplicate settings of ${project.name}`}
                  disabled={duplicateMutation.isPending && duplicatingId === project.id}
                  onClick={() => {
                    setDuplicatingId(project.id);
                    setLastResult(null);
                    duplicateMutation.mutate(project.id, {
                      onSuccess: (created) => {
                        setDuplicatingId(null);
                        setLastResult({
                          kind: "duplicated",
                          projectId: created.id,
                          name: created.name,
                        });
                      },
                      onError: () => setDuplicatingId(null),
                    });
                  }}
                >
                  {duplicateMutation.isPending && duplicatingId === project.id ? (
                    <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Copy className="size-3.5" aria-hidden="true" />
                  )}
                  Duplicate settings
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </ResourceCard>
  );
}
