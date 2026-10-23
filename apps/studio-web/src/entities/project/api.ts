import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiGet, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type Project = components["schemas"]["ProjectOut"];
export type ProjectCreateRequest = components["schemas"]["ProjectCreateIn"];
export type ProjectRevision = components["schemas"]["RevisionOut"];

export function useProjects() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projects(sessionVersion),
    queryFn: ({ signal }) => apiGet("/api/v1/projects", { signal }),
  });
}

export function useProjectRevisions(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectRevisions(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/revisions", {
        signal,
        pathParams: { project_id: projectId },
      }),
    enabled: Boolean(projectId),
  });
}

export function useCreateProject() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.projectActions(sessionVersion),
    mutationFn: ({ request }: { request: ProjectCreateRequest; sessionVersion: number }) =>
      apiPost("/api/v1/projects", request),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projects(variables.sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: ProjectCreateRequest,
      callbacks?: {
        onSuccess?: (project: Project) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { request, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useDuplicateProject() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.projectActions(sessionVersion),
    mutationFn: ({ projectId }: { projectId: string; sessionVersion: number }) =>
      apiPost("/api/v1/projects/{project_id}/duplicate", undefined, {
        pathParams: { project_id: projectId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projects(variables.sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      projectId: string,
      callbacks?: {
        onSuccess?: (project: Project) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { projectId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}
