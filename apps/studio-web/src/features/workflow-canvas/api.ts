import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiGet, apiPatch, apiPost, apiPut } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type WorkflowCreateRequest = components["schemas"]["WorkflowCreateIn"];
export type WorkflowCreateResponse = components["schemas"]["WorkflowCreateOut"];
export type WorkflowGraphUpdateRequest = components["schemas"]["WorkflowGraphUpdateIn"];
export type WorkflowGraphUpdateResponse = components["schemas"]["WorkflowGraphUpdateOut"];
export type WorkflowLayoutSaveResponse = components["schemas"]["WorkflowLayoutSaveOut"];
export type WorkflowRunRequest = components["schemas"]["WorkflowRunRequestIn"];
export type WorkflowDryRunResponse = components["schemas"]["WorkflowDryRunOut"];
export type WorkflowExecutionResponse = components["schemas"]["WorkflowExecutionOut"];
export type WorkflowRunResponse = WorkflowDryRunResponse | WorkflowExecutionResponse;
export type WorkflowApprovalResponse = components["schemas"]["WorkflowApprovalOut"];

export function useProjectWorkflow(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.projectWorkflow(sessionVersion, projectId),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/projects/{project_id}/workflows", {
        signal,
        pathParams: { project_id: projectId },
      }),
    retry: false,
  });
}

export function useCreateWorkflow(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.workflowActions(sessionVersion, projectId),
    mutationFn: ({
      request,
      projectId: scopeProjectId,
    }: {
      request: WorkflowCreateRequest;
      projectId: string;
      sessionVersion: number;
    }) =>
      apiPost("/api/v1/projects/{project_id}/workflows", request, {
        pathParams: { project_id: scopeProjectId },
      }),
    onSuccess: (data, variables) => {
      queryClient.setQueryData(
        queryKeys.projectWorkflow(variables.sessionVersion, variables.projectId),
        data,
      );
    },
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: WorkflowCreateRequest,
      callbacks?: {
        onSuccess?: (workflow: WorkflowCreateResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { request, projectId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useSaveWorkflowLayout(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.workflowActions(sessionVersion, projectId),
    mutationFn: ({
      workflowId,
      uiLayout,
      projectId: scopeProjectId,
    }: {
      workflowId: string;
      uiLayout: Record<string, unknown>;
      projectId: string;
      sessionVersion: number;
    }) =>
      apiPatch<WorkflowLayoutSaveResponse>(
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/layout",
        { ui_layout: uiLayout },
        {
          pathParams: { project_id: scopeProjectId, workflow_id: workflowId },
        },
      ),
    onSuccess: (data, variables) => {
      queryClient.setQueryData<WorkflowCreateResponse | undefined>(
        queryKeys.projectWorkflow(variables.sessionVersion, variables.projectId),
        (current) =>
          current && current.id === variables.workflowId
            ? {
                ...current,
                current_version: data.version_number,
                ui_layout: variables.uiLayout as WorkflowCreateResponse["ui_layout"],
              }
            : current,
      );
    },
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      workflowId: string,
      uiLayout: Record<string, unknown>,
      callbacks?: {
        onSuccess?: (result: WorkflowLayoutSaveResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { workflowId, uiLayout, projectId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useUpdateWorkflowGraph(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.workflowActions(sessionVersion, projectId),
    mutationFn: ({
      workflowId,
      request,
      projectId: scopeProjectId,
    }: {
      workflowId: string;
      request: WorkflowGraphUpdateRequest;
      projectId: string;
    }) =>
      apiPut<WorkflowGraphUpdateResponse>(
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/graph",
        request,
        {
          pathParams: { project_id: scopeProjectId, workflow_id: workflowId },
        },
      ),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      workflowId: string,
      request: WorkflowGraphUpdateRequest,
      callbacks?: {
        onSuccess?: (result: WorkflowGraphUpdateResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { workflowId, request, projectId },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate],
  );
  return { ...mutation, mutate };
}

export function useExecuteWorkflow(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.workflowActions(sessionVersion, projectId),
    mutationFn: ({
      workflowId,
      request,
      projectId: scopeProjectId,
    }: {
      workflowId: string;
      request: WorkflowRunRequest;
      projectId: string;
    }) =>
      apiPost("/api/v1/projects/{project_id}/workflows/{workflow_id}/runs", request, {
        pathParams: { project_id: scopeProjectId, workflow_id: workflowId },
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      workflowId: string,
      request: WorkflowRunRequest,
      callbacks?: {
        onSuccess?: (result: WorkflowRunResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { workflowId, request, projectId },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate],
  );
  return { ...mutation, mutate };
}

export function useApproveWorkflowGate(projectId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const mutation = useMutation({
    mutationKey: queryKeys.workflowActions(sessionVersion, projectId),
    mutationFn: ({
      workflowId,
      runId,
      nodeId,
      projectId: scopeProjectId,
    }: {
      workflowId: string;
      runId: string;
      nodeId: string;
      projectId: string;
    }) =>
      apiPost(
        "/api/v1/projects/{project_id}/workflows/{workflow_id}/runs/{run_id}/nodes/{node_id}/approve",
        undefined,
        {
          pathParams: {
            project_id: scopeProjectId,
            workflow_id: workflowId,
            run_id: runId,
            node_id: nodeId,
          },
        },
      ),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      workflowId: string,
      runId: string,
      nodeId: string,
      callbacks?: {
        onSuccess?: (result: WorkflowApprovalResponse) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { workflowId, runId, nodeId, projectId },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [projectId, rawMutate],
  );
  return { ...mutation, mutate };
}
