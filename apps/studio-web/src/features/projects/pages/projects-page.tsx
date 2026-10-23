import { Link } from "@tanstack/react-router";
import {
  BookOpenText,
  Calendar,
  Clapperboard,
  Copy,
  FileCode2,
  Film,
  FolderCog,
  FolderPlus,
  GitBranch,
  History,
  LayoutGrid,
  ListChecks,
  LoaderCircle,
  Sparkles,
} from "lucide-react";
import { useState } from "react";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import {
  useCreateProject,
  useDuplicateProject,
  useProjectRevisions,
  useProjects,
} from "@/entities/project/api";
import { authErrorMessage } from "@/lib/api/errors";
import { useSessionStore } from "@/lib/stores/session-store";

interface ProjectsPageProps {
  projectId: string;
}

export function ProjectsPage({ projectId }: ProjectsPageProps) {
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const projectsQuery = useProjects();
  const revisionsQuery = useProjectRevisions(projectId);
  const duplicateMutation = useDuplicateProject();
  const createMutation = useCreateProject();

  const [duplicatedProjectId, setDuplicatedProjectId] = useState<string | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newAspectRatio, setNewAspectRatio] = useState("16:9");
  const [newFps, setNewFps] = useState(24);
  const [newStylePreset, setNewStylePreset] = useState("2d_anime_cinematic");
  const [newOutputProfile, setNewOutputProfile] = useState("1080p_h264");

  const currentProject = projectsQuery.data?.find((item) => item.id === projectId);
  const revisions = revisionsQuery.data ?? [];

  return (
    <div data-testid="projects-page" className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-border pb-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FolderCog className="size-5 text-primary" aria-hidden="true" />
            <h2 className="text-xl font-semibold tracking-tight">
              Project Settings & Revision History
            </h2>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            Inspect active workspace configuration, immutable script/pipeline revision history, or
            clone this workspace into an isolated branch.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={duplicateMutation.isPending}
            onClick={() => {
              setDuplicatedProjectId(null);
              duplicateMutation.mutate(projectId, {
                onSuccess: (created) => setDuplicatedProjectId(created.id),
              });
            }}
          >
            {duplicateMutation.isPending ? (
              <>
                <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                Duplicating...
              </>
            ) : (
              <>
                <Copy className="size-4" aria-hidden="true" />
                Duplicate project
              </>
            )}
          </Button>
          <Button
            type="button"
            size="sm"
            variant={showNewForm ? "secondary" : "default"}
            onClick={() => {
              createMutation.reset();
              setShowNewForm((current) => !current);
            }}
          >
            <FolderPlus className="size-4" aria-hidden="true" />
            {showNewForm ? "Close form" : "New workspace"}
          </Button>
        </div>
      </div>

      {duplicatedProjectId && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-xs text-foreground"
        >
          <span>
            Created duplicate project <strong className="font-mono">{duplicatedProjectId}</strong>{" "}
            with cloned Story Bible and shot list.
          </span>
          <Link
            to="/projects/$projectId/$module"
            params={{ projectId: duplicatedProjectId, module: "shot-board" }}
            className="font-semibold text-primary underline underline-offset-4"
          >
            Switch to duplicated project →
          </Link>
        </div>
      )}

      {duplicateMutation.isError && (
        <p role="alert" className="text-xs text-destructive">
          {authErrorMessage(duplicateMutation.error, activeAdminToken, "duplicate project")}
        </p>
      )}

      {showNewForm && (
        <form
          aria-label="Create new workspace"
          className="space-y-4 rounded-xl border border-primary/20 bg-secondary/40 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (!newName.trim() || createMutation.isPending) return;
            createMutation.mutate(
              {
                name: newName.trim(),
                description: newDescription.trim(),
                aspect_ratio: newAspectRatio,
                fps: newFps,
                language: "vi",
                style_preset: newStylePreset,
                output_profile: newOutputProfile,
              },
              {
                onSuccess: (created) => {
                  setNewName("");
                  setNewDescription("");
                  setShowNewForm(false);
                  setDuplicatedProjectId(created.id);
                },
              },
            );
          }}
        >
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold">
              <Sparkles className="size-4 text-primary" aria-hidden="true" />
              Create another production workspace
            </h3>
            <span className="text-xs text-muted-foreground">POST /api/v1/projects</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>Project name</span>
              <input
                required
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="e.g. Episode 02 — Glass Citadel"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Style preset</span>
              <select
                value={newStylePreset}
                onChange={(event) => setNewStylePreset(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="2d_anime_cinematic">2d_anime_cinematic</option>
                <option value="3d_pixar_stylized">3d_pixar_stylized</option>
                <option value="photoreal_film_noir">photoreal_film_noir</option>
                <option value="cyberpunk_neon_anime">cyberpunk_neon_anime</option>
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium sm:col-span-3">
              <span>Description</span>
              <input
                value={newDescription}
                onChange={(event) => setNewDescription(event.target.value)}
                placeholder="Production notes or target audience"
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Aspect ratio</span>
              <select
                value={newAspectRatio}
                onChange={(event) => setNewAspectRatio(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="16:9">16:9</option>
                <option value="9:16">9:16</option>
                <option value="1:1">1:1</option>
                <option value="21:9">21:9</option>
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Frame rate</span>
              <select
                value={newFps}
                onChange={(event) => setNewFps(Number(event.target.value))}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value={24}>24 fps</option>
                <option value={25}>25 fps</option>
                <option value={30}>30 fps</option>
                <option value={60}>60 fps</option>
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>Output profile</span>
              <select
                value={newOutputProfile}
                onChange={(event) => setNewOutputProfile(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="1080p_h264">1080p_h264</option>
                <option value="720p_h264">720p_h264</option>
                <option value="4k_h264">4k_h264</option>
              </select>
            </label>
          </div>

          {createMutation.isError && (
            <p role="alert" className="text-xs text-destructive">
              {authErrorMessage(createMutation.error, activeAdminToken, "create project")}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setShowNewForm(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={!newName.trim() || createMutation.isPending}>
              Create workspace
            </Button>
          </div>
        </form>
      )}

      {currentProject && (
        <section
          aria-label="Active project profile"
          className="grid gap-4 rounded-xl border border-border bg-secondary/25 p-5 sm:grid-cols-2 lg:grid-cols-4"
        >
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Project Name & ID</p>
            <p className="text-sm font-semibold">{currentProject.name}</p>
            <p className="font-mono text-xs text-muted-foreground">{currentProject.id}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Format & Frame Rate</p>
            <p className="text-sm font-semibold">
              {currentProject.aspect_ratio} · {currentProject.fps} fps
            </p>
            <p className="font-mono text-xs text-muted-foreground">
              {currentProject.output_profile} · lang: {currentProject.language}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">Style Preset</p>
            <p className="font-mono text-sm font-semibold text-primary">
              {currentProject.style_preset}
            </p>
            <p className="text-xs text-muted-foreground">
              {currentProject.description || "No custom logline configured"}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">State & Assets</p>
            <p className="text-sm font-semibold">Revision #{currentProject.current_revision_num}</p>
            <p className="text-xs text-muted-foreground">
              {currentProject.asset_count} registered media assets
            </p>
          </div>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section aria-label="Revision history" className="space-y-3 lg:col-span-2">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <History className="size-4 text-primary" aria-hidden="true" />
              Pipeline Revision History ({revisions.length})
            </h3>
            <span className="text-xs text-muted-foreground">
              GET /api/v1/projects/{projectId}/revisions
            </span>
          </div>

          <QueryState
            isLoading={revisionsQuery.isPending}
            isError={revisionsQuery.isError}
            isEmpty={revisions.length === 0}
            hasData={revisions.length > 0}
            loadingMessage="Loading project revisions..."
            errorMessage={authErrorMessage(
              revisionsQuery.error,
              activeAdminToken,
              "project revisions",
            )}
            emptyTitle="No revisions committed yet"
            emptyDescription="Import a script in Script Studio or modify Story Bible entries to record an immutable pipeline revision."
            onRetry={() => void revisionsQuery.refetch()}
          >
            <ul className="space-y-3">
              {revisions.map((rev) => {
                const snapshotEntries = Object.entries(rev.snapshot_json ?? {});
                return (
                  <li
                    key={rev.id}
                    className="rounded-xl border border-border bg-background p-4 shadow-2xs"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="rounded-md bg-primary/10 px-2 py-0.5 font-mono text-xs font-semibold text-primary">
                          r{rev.revision_number}
                        </span>
                        <p className="text-sm font-semibold text-foreground">
                          {rev.commit_message}
                        </p>
                      </div>
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <Calendar className="size-3.5" aria-hidden="true" />
                        {new Date(rev.created_at).toLocaleString()}
                      </span>
                    </div>

                    {snapshotEntries.length > 0 && (
                      <dl className="mt-3 grid grid-cols-2 gap-2 rounded-lg border border-border bg-secondary/40 p-2.5 text-xs sm:grid-cols-4">
                        {snapshotEntries.slice(0, 8).map(([key, value]) => (
                          <div key={key} className="min-w-0">
                            <dt className="truncate font-mono text-[11px] text-muted-foreground">
                              {key}
                            </dt>
                            <dd className="truncate font-medium text-foreground">
                              {typeof value === "object" && value !== null
                                ? Array.isArray(value)
                                  ? `${value.length} items`
                                  : "object"
                                : String(value)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </li>
                );
              })}
            </ul>
          </QueryState>
        </section>

        <section aria-label="Workspace switcher" className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <Film className="size-4 text-primary" aria-hidden="true" />
              All Workspaces ({projectsQuery.data?.length ?? 0})
            </h3>
          </div>

          <div className="space-y-2.5">
            {(projectsQuery.data ?? []).map((item) => {
              const isCurrent = item.id === projectId;
              return (
                <div
                  key={item.id}
                  className={
                    isCurrent
                      ? "rounded-xl border border-primary/40 bg-primary/5 p-3.5"
                      : "rounded-xl border border-border bg-background p-3.5"
                  }
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold">{item.name}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {item.aspect_ratio} · {item.fps} fps · r{item.current_revision_num}
                      </p>
                    </div>
                    {isCurrent && (
                      <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground">
                        Active
                      </span>
                    )}
                  </div>

                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "script-studio" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <BookOpenText className="size-3" aria-hidden="true" />
                      Script
                    </Link>
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "shot-board" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <LayoutGrid className="size-3" aria-hidden="true" />
                      Shots
                    </Link>
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "workflow-canvas" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <GitBranch className="size-3" aria-hidden="true" />
                      DAG
                    </Link>
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "queue-monitor" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <ListChecks className="size-3" aria-hidden="true" />
                      Queue
                    </Link>
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "timeline-editor" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <Clapperboard className="size-3" aria-hidden="true" />
                      Timeline
                    </Link>
                    <Link
                      to="/projects/$projectId/$module"
                      params={{ projectId: item.id, module: "projects" }}
                      className="inline-flex h-7 items-center gap-1 rounded-md border border-border bg-card px-2 text-xs font-medium hover:bg-secondary"
                    >
                      <FileCode2 className="size-3" aria-hidden="true" />
                      Revisions
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}

export default ProjectsPage;
