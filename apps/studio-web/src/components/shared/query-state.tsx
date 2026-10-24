import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";

interface QueryStateProps {
  isLoading: boolean;
  isError: boolean;
  isEmpty: boolean;
  /**
   * Previously loaded data is still available. A failed background refresh then shows an
   * inline alert above the last data instead of replacing it.
   */
  hasData?: boolean;
  loadingMessage?: string;
  errorMessage: string;
  emptyTitle: string;
  emptyDescription: string;
  onRetry: () => void;
  children: ReactNode;
}

export function QueryState({
  isLoading,
  isError,
  isEmpty,
  hasData = false,
  loadingMessage,
  errorMessage,
  emptyTitle,
  emptyDescription,
  onRetry,
  children,
}: QueryStateProps) {
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <p role="status" className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        {loadingMessage ?? t("queryState.loading")}
      </p>
    );
  }

  const errorAlert = isError ? (
    <div
      role="alert"
      className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4"
    >
      <div className="flex items-start gap-2 text-sm text-muted-foreground">
        <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
        <p>
          {hasData && t("queryState.refreshFailedWithData")}
          {errorMessage}
        </p>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        {t("queryState.retry")}
      </Button>
    </div>
  ) : null;

  if (isError && !hasData) return errorAlert;

  if (isEmpty) {
    return (
      <div className="space-y-3">
        {errorAlert}
        <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
          <p className="text-sm font-medium">{emptyTitle}</p>
          <p className="mt-1 text-sm text-muted-foreground">{emptyDescription}</p>
        </div>
      </div>
    );
  }

  if (!errorAlert) return children;
  return (
    <div className="space-y-3">
      {errorAlert}
      {children}
    </div>
  );
}
