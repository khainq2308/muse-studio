import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/queryKeys";
import type { components } from "@/lib/api/schema";
import { useSessionStore } from "@/lib/stores/session-store";

export type Connection = components["schemas"]["ConnectionOut"];
export type ConnectionCreateRequest = components["schemas"]["ConnectionCreateIn"];
export type ConnectionAccount = components["schemas"]["AccountOut"];
export type AccountCreateRequest = components["schemas"]["AccountCreateIn"];
export type AccountUpdateRequest = components["schemas"]["AccountUpdateIn"];

export function useConnections() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.connections(sessionVersion),
    queryFn: ({ signal }) => apiGet("/api/v1/connections", { signal }),
  });
}

export function useConnectionAccounts(connectionId: string | null) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  return useQuery({
    queryKey: queryKeys.connectionAccounts(sessionVersion, connectionId ?? ""),
    queryFn: ({ signal }) =>
      apiGet("/api/v1/connections/{connection_id}/accounts", {
        signal,
        pathParams: { connection_id: connectionId! },
      }),
    enabled: Boolean(connectionId),
  });
}

export function useCreateConnection() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({ request }: { request: ConnectionCreateRequest; sessionVersion: number }) =>
      apiPost("/api/v1/connections", request),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.connections(variables.sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.metricsSummary(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: ConnectionCreateRequest,
      callbacks?: {
        onSuccess?: (connection: Connection) => void;
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

export function useTestConnection() {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({ connectionId }: { connectionId: string; sessionVersion: number }) =>
      apiPost("/api/v1/connections/{connection_id}/test", undefined, {
        pathParams: { connection_id: connectionId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.connections(variables.sessionVersion),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.connectionAccounts(variables.sessionVersion, variables.connectionId),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      connectionId: string,
      callbacks?: {
        onSuccess?: (connection: Connection) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { connectionId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useCreateAccount(connectionId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({
      request,
      connectionId: scopeConnectionId,
    }: {
      request: AccountCreateRequest;
      connectionId: string;
      sessionVersion: number;
    }) =>
      apiPost("/api/v1/connections/{connection_id}/accounts", request, {
        pathParams: { connection_id: scopeConnectionId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.connectionAccounts(variables.sessionVersion, variables.connectionId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.connections(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      request: AccountCreateRequest,
      callbacks?: {
        onSuccess?: (account: ConnectionAccount) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { request, connectionId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [connectionId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useUpdateAccount(connectionId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({
      accountId,
      update,
      connectionId: scopeConnectionId,
    }: {
      accountId: string;
      update: AccountUpdateRequest;
      connectionId: string;
      sessionVersion: number;
    }) =>
      apiPatch<ConnectionAccount>(
        "/api/v1/connections/{connection_id}/accounts/{account_id}",
        update,
        {
          pathParams: { connection_id: scopeConnectionId, account_id: accountId },
        },
      ),
    onSettled: (_data, _error, variables) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.connectionAccounts(variables.sessionVersion, variables.connectionId),
      }),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      accountId: string,
      update: AccountUpdateRequest,
      callbacks?: {
        onSuccess?: (account: ConnectionAccount) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { accountId, update, connectionId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [connectionId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useDeleteAccount(connectionId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({
      accountId,
      connectionId: scopeConnectionId,
    }: {
      accountId: string;
      connectionId: string;
      sessionVersion: number;
    }) =>
      apiDelete<null>("/api/v1/connections/{connection_id}/accounts/{account_id}", {
        pathParams: { connection_id: scopeConnectionId, account_id: accountId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.connectionAccounts(variables.sessionVersion, variables.connectionId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.connections(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      accountId: string,
      callbacks?: { onSuccess?: () => void; onError?: (error: unknown) => void },
    ) =>
      rawMutate(
        { accountId, connectionId, sessionVersion },
        {
          onSuccess: () => callbacks?.onSuccess?.(),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [connectionId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}

export function useTestAccount(connectionId: string) {
  const sessionVersion = useSessionStore((state) => state.sessionVersion);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: queryKeys.connectionActions(sessionVersion),
    mutationFn: ({
      accountId,
      connectionId: scopeConnectionId,
    }: {
      accountId: string;
      connectionId: string;
      sessionVersion: number;
    }) =>
      apiPost("/api/v1/connections/{connection_id}/accounts/{account_id}/test", undefined, {
        pathParams: { connection_id: scopeConnectionId, account_id: accountId },
      }),
    onSettled: (_data, _error, variables) =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.connectionAccounts(variables.sessionVersion, variables.connectionId),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.connections(variables.sessionVersion),
        }),
      ]),
  });
  const { mutate: rawMutate } = mutation;
  const mutate = useCallback(
    (
      accountId: string,
      callbacks?: {
        onSuccess?: (account: ConnectionAccount) => void;
        onError?: (error: unknown) => void;
      },
    ) =>
      rawMutate(
        { accountId, connectionId, sessionVersion },
        {
          onSuccess: (data) => callbacks?.onSuccess?.(data),
          onError: (error) => callbacks?.onError?.(error),
        },
      ),
    [connectionId, rawMutate, sessionVersion],
  );
  return { ...mutation, mutate };
}
