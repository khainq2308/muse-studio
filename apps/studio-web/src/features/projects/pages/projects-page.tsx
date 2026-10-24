import { Link } from "@tanstack/react-router";
import { ArrowRight, FolderPlus, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/shared/page-header";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useCreateProject, useProjects } from "@/entities/project/api";
import {
  PROJECT_ASPECT_RATIO_OPTIONS,
  PROJECT_CREATE_DEFAULTS,
  PROJECT_FPS_OPTIONS,
  PROJECT_LANGUAGE_OPTIONS,
  PROJECT_OUTPUT_PROFILE_HINT,
  PROJECT_STYLE_PRESET_OPTIONS,
} from "@/entities/project/options";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/i18n/formatters";
import { useSessionStore } from "@/lib/stores/session-store";

/** Workspace-level project list: find a project, open it, or create a new one. */
export function ProjectsPage() {
  const { t } = useTranslation("projects");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const projectsQuery = useProjects();
  const createMutation = useCreateProject();

  const [created, setCreated] = useState<{ projectId: string; name: string } | null>(null);
  const [showNewForm, setShowNewForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newAspectRatio, setNewAspectRatio] = useState<string>(
    PROJECT_CREATE_DEFAULTS.aspect_ratio,
  );
  const [newFps, setNewFps] = useState<number>(PROJECT_CREATE_DEFAULTS.fps);
  const [newStylePreset, setNewStylePreset] = useState<string>(
    PROJECT_CREATE_DEFAULTS.style_preset,
  );
  const [newLanguage, setNewLanguage] = useState<string>(PROJECT_CREATE_DEFAULTS.language);

  // Most recently changed first, so the project in progress is at the top.
  const projects = [...(projectsQuery.data ?? [])].sort((a, b) =>
    b.updated_at.localeCompare(a.updated_at),
  );

  return (
    <div data-testid="projects-page" className="space-y-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
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
              {showNewForm ? t("closeForm") : t("newProject")}
            </Button>
          </>
        }
      />

      {created && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-xs text-foreground"
        >
          <span>{t("createdNotice", { name: created.name, projectId: created.projectId })}</span>
          <Link
            to="/projects/$projectId/$module"
            params={{ projectId: created.projectId, module: "script-studio" }}
            className="font-semibold text-primary underline underline-offset-4"
          >
            {t("startWithScript")}
          </Link>
        </div>
      )}

      {showNewForm && (
        <form
          aria-label={t("form.title")}
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
                language: newLanguage,
                style_preset: newStylePreset,
                output_profile: PROJECT_CREATE_DEFAULTS.output_profile,
              },
              {
                onSuccess: (created) => {
                  setNewName("");
                  setNewDescription("");
                  setShowNewForm(false);
                  setCreated({ projectId: created.id, name: created.name });
                },
              },
            );
          }}
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
            <h3 className="flex min-w-0 items-center gap-1.5 text-sm font-semibold">
              <Sparkles className="size-4 shrink-0 text-primary" aria-hidden="true" />
              <span>{t("form.title")}</span>
            </h3>
            <span className="break-all sm:text-right text-xs text-muted-foreground">
              {t("form.endpoint")}
            </span>
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <label className="space-y-1 text-xs font-medium sm:col-span-2">
              <span>{t("form.name")}</span>
              <input
                required
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder={t("form.namePlaceholder")}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>{t("form.stylePreset")}</span>
              <select
                value={newStylePreset}
                onChange={(event) => setNewStylePreset(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_STYLE_PRESET_OPTIONS.map((preset) => (
                  <option key={preset} value={preset}>
                    {preset}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium sm:col-span-3">
              <span>{t("form.description")}</span>
              <input
                value={newDescription}
                onChange={(event) => setNewDescription(event.target.value)}
                placeholder={t("form.descriptionPlaceholder")}
                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>{t("form.aspectRatio")}</span>
              <select
                value={newAspectRatio}
                onChange={(event) => setNewAspectRatio(event.target.value)}
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
              <span>{t("form.frameRate")}</span>
              <select
                value={newFps}
                onChange={(event) => setNewFps(Number(event.target.value))}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_FPS_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {t("form.fpsUnit", { value })}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-xs font-medium">
              <span>{t("form.language")}</span>
              <select
                value={newLanguage}
                onChange={(event) => setNewLanguage(event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {PROJECT_LANGUAGE_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <div className="space-y-1 text-xs font-medium sm:col-span-3">
              <span>{t("form.outputProfile")}</span>
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

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setShowNewForm(false)}>
              {t("form.cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={!newName.trim() || createMutation.isPending}>
              {createMutation.isPending ? t("form.submitting") : t("form.submit")}
            </Button>
          </div>
        </form>
      )}

      <section
        aria-label={t("allProjects.title", {
          count: projects.length,
          defaultValue: "All projects",
        })}
        className="space-y-3"
      >
        <h3 className="text-base font-semibold">
          {t("allProjects.title", { count: projects.length })}
        </h3>
        <QueryState
          isLoading={projectsQuery.isPending}
          isError={projectsQuery.isError}
          isEmpty={projects.length === 0}
          hasData={projects.length > 0}
          loadingMessage={t("allProjects.loading")}
          errorMessage={authErrorMessage(projectsQuery.error, activeAdminToken, "projects")}
          emptyTitle={t("allProjects.emptyTitle")}
          emptyDescription={t("allProjects.emptyDescription")}
          onRetry={() => void projectsQuery.refetch()}
        >
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {projects.map((item) => (
              <li
                key={item.id}
                className="flex flex-col justify-between gap-3 rounded-xl border border-border bg-background p-4 shadow-2xs"
              >
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm font-semibold">{item.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {item.aspect_ratio} · {item.fps} fps · {item.style_preset}
                  </p>
                  {item.description && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{item.description}</p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {t("allProjects.updated", { time: formatDateTime(item.updated_at) })}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: item.id, module: "shot-board" }}
                    aria-label={t("allProjects.openAria", { name: item.name })}
                    className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-semibold text-primary-foreground shadow-xs transition-opacity hover:opacity-90"
                  >
                    {t("allProjects.open")}
                    <ArrowRight className="size-3.5" aria-hidden="true" />
                  </Link>
                  <Link
                    to="/projects/$projectId/$module"
                    params={{ projectId: item.id, module: "settings" }}
                    className="inline-flex h-8 items-center rounded-lg border border-border px-3 text-xs font-medium hover:bg-secondary"
                  >
                    {t("allProjects.settings")}
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        </QueryState>
      </section>
    </div>
  );
}

export default ProjectsPage;
