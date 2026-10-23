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
import { authErrorMessage } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

interface ProjectListProps {
  projects: components["schemas"]["ProjectOut"][];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
}

const ASPECT_RATIO_OPTIONS = ["16:9", "9:16", "1:1", "21:9", "4:3"] as const;
const FPS_OPTIONS = [24, 25, 30, 60] as const;
const STYLE_PRESET_OPTIONS = [
  "2d_anime_cinematic",
  "3d_pixar_stylized",
  "photoreal_film_noir",
  "cyberpunk_neon_anime",
  "watercolor_storybook",
] as const;
const OUTPUT_PROFILE_OPTIONS = ["1080p_h264", "720p_h264", "4k_h264"] as const;

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
  const [aspectRatio, setAspectRatio] = useState("16:9");
  const [fps, setFps] = useState(24);
  const [language, setLanguage] = useState("vi");
  const [stylePreset, setStylePreset] = useState("2d_anime_cinematic");
  const [outputProfile, setOutputProfile] = useState("1080p_h264");
  const [duplicatingId, setDuplicatingId] = useState<string | null>(null);

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
            createMutation.mutate(
              {
                name: name.trim(),
                description: description.trim(),
                aspect_ratio: aspectRatio,
                fps,
                language,
                style_preset: stylePreset,
                output_profile: outputProfile,
              },
              {
                onSuccess: () => {
                  setName("");
                  setDescription("");
                  setShowCreateForm(false);
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
                {ASPECT_RATIO_OPTIONS.map((ratio) => (
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
                {FPS_OPTIONS.map((value) => (
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
                {STYLE_PRESET_OPTIONS.map((preset) => (
                  <option key={preset} value={preset}>
                    {preset}
                  </option>
                ))}
              </select>
            </label>

            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1 text-xs font-medium">
                <span>Output profile</span>
                <select
                  value={outputProfile}
                  onChange={(event) => setOutputProfile(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {OUTPUT_PROFILE_OPTIONS.map((profile) => (
                    <option key={profile} value={profile}>
                      {profile}
                    </option>
                  ))}
                </select>
              </label>

              <label className="space-y-1 text-xs font-medium">
                <span>Language</span>
                <select
                  value={language}
                  onChange={(event) => setLanguage(event.target.value)}
                  className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <option value="vi">vi</option>
                  <option value="en">en</option>
                  <option value="ja">ja</option>
                </select>
              </label>
            </div>
          </div>

          {createMutation.isError && (
            <p role="alert" className="text-xs text-destructive">
              {authErrorMessage(createMutation.error, activeAdminToken, "create project")}
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

      {duplicateMutation.isError && (
        <p role="alert" className="mb-3 text-xs text-destructive">
          {authErrorMessage(duplicateMutation.error, activeAdminToken, "duplicate project")}
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
                  disabled={duplicateMutation.isPending && duplicatingId === project.id}
                  onClick={() => {
                    setDuplicatingId(project.id);
                    duplicateMutation.mutate(project.id, {
                      onSuccess: () => setDuplicatingId(null),
                      onError: () => setDuplicatingId(null),
                    });
                  }}
                >
                  {duplicateMutation.isPending && duplicatingId === project.id ? (
                    <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : (
                    <Copy className="size-3.5" aria-hidden="true" />
                  )}
                  Duplicate
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </QueryState>
    </ResourceCard>
  );
}
