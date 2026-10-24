import { Link } from "@tanstack/react-router";
import { Calendar, Copy, History, LoaderCircle } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/shared/page-header";
import { QueryState } from "@/components/shared/query-state";
import { Button } from "@/components/ui/button";
import { useDuplicateProject, useProjects } from "@/entities/project/api";
import { PROJECT_DUPLICATE_SCOPE_NOTE } from "@/entities/project/options";
import { useProjectRevisions } from "@/features/projects/api";
import { actionErrorMessage, authErrorMessage } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/i18n/formatters";
import { useSessionStore } from "@/lib/stores/session-store";

interface ProjectSettingsPageProps {
  projectId: string;
}

/** Settings of the open project: profile, revision history and duplication. */
export function ProjectSettingsPage({ projectId }: ProjectSettingsPageProps) {
  const { t } = useTranslation("projects");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const projectsQuery = useProjects();
  const revisionsQuery = useProjectRevisions(projectId);
  const duplicateMutation = useDuplicateProject();
  const [duplicated, setDuplicated] = useState<{ projectId: string; name: string } | null>(null);

  const currentProject = projectsQuery.data?.find((item) => item.id === projectId);
  const revisions = revisionsQuery.data ?? [];

  return (
    <div data-testid="project-settings-page" className="space-y-6">
      <PageHeader
        title={t("settings.title")}
        description={t("settings.description")}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={duplicateMutation.isPending}
              onClick={() => {
                setDuplicated(null);
                duplicateMutation.mutate(projectId, {
                  onSuccess: (created) =>
                    setDuplicated({ projectId: created.id, name: created.name }),
                });
              }}
            >
              {duplicateMutation.isPending ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  {t("settings.duplicating")}
                </>
              ) : (
                <>
                  <Copy className="size-4" aria-hidden="true" />
                  {t("settings.duplicate")}
                </>
              )}
            </Button>
          </>
        }
      />

      {duplicated && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3 text-xs text-foreground"
        >
          <span>
            {t("settings.duplicatedNotice", {
              name: duplicated.name,
              projectId: duplicated.projectId,
              scopeNote: PROJECT_DUPLICATE_SCOPE_NOTE,
            })}
          </span>
          <Link
            to="/projects/$projectId/$module"
            params={{ projectId: duplicated.projectId, module: "script-studio" }}
            className="font-semibold text-primary underline underline-offset-4"
          >
            {t("settings.openCopyInScript")}
          </Link>
        </div>
      )}

      {duplicateMutation.isError && (
        <p role="alert" className="text-xs text-destructive">
          {actionErrorMessage(duplicateMutation.error, activeAdminToken, "duplicate project")}
        </p>
      )}

      {currentProject && (
        <section
          aria-label={t("settings.profile.ariaLabel")}
          className="grid gap-4 rounded-xl border border-border bg-secondary/25 p-5 sm:grid-cols-2 lg:grid-cols-4"
        >
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">
              {t("settings.profile.nameAndId")}
            </p>
            <p className="text-sm font-semibold">{currentProject.name}</p>
            <p className="font-mono text-xs text-muted-foreground">{currentProject.id}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">
              {t("settings.profile.formatAndFps")}
            </p>
            <p className="text-sm font-semibold">
              {currentProject.aspect_ratio} · {currentProject.fps} fps
            </p>
            <p className="font-mono text-xs text-muted-foreground">
              {currentProject.output_profile} ·{" "}
              {t("settings.profile.langInfo", { language: currentProject.language })}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">
              {t("settings.profile.stylePreset")}
            </p>
            <p className="font-mono text-sm font-semibold text-primary">
              {currentProject.style_preset}
            </p>
            <p className="text-xs text-muted-foreground">
              {currentProject.description || t("settings.profile.noDescription")}
            </p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">
              {t("settings.profile.stateAndAssets")}
            </p>
            <p className="text-sm font-semibold">
              {t("settings.profile.revisionNum", { number: currentProject.current_revision_num })}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("settings.profile.assetCount", { count: currentProject.asset_count })}
            </p>
          </div>
        </section>
      )}

      <section aria-label={t("settings.revisions.ariaLabel")} className="space-y-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
          <h3 className="flex min-w-0 items-center gap-2 text-base font-semibold">
            <History className="size-4 shrink-0 text-primary" aria-hidden="true" />
            <span>{t("settings.revisions.title", { count: revisions.length })}</span>
          </h3>
          <span className="break-all sm:text-right text-xs text-muted-foreground">
            {t("settings.revisions.endpoint", { projectId })}
          </span>
        </div>

        <QueryState
          isLoading={revisionsQuery.isPending}
          isError={revisionsQuery.isError}
          isEmpty={revisions.length === 0}
          hasData={revisions.length > 0}
          loadingMessage={t("settings.revisions.loading")}
          errorMessage={authErrorMessage(
            revisionsQuery.error,
            activeAdminToken,
            "project revisions",
          )}
          emptyTitle={t("settings.revisions.emptyTitle")}
          emptyDescription={t("settings.revisions.emptyDescription")}
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
                      <p className="text-sm font-semibold text-foreground">{rev.commit_message}</p>
                    </div>
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <Calendar className="size-3.5" aria-hidden="true" />
                      {formatDateTime(rev.created_at)}
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
                                ? t("settings.revisions.itemsCount", { count: value.length })
                                : t("settings.revisions.objectLabel")
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
    </div>
  );
}

export default ProjectSettingsPage;
