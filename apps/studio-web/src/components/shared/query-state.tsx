import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface QueryStateProps {
  isLoading: boolean;
  isError: boolean;
  isEmpty: boolean;
  loadingMessage: string;
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
  loadingMessage,
  errorMessage,
  emptyTitle,
  emptyDescription,
  onRetry,
  children,
}: QueryStateProps) {
  if (isLoading) {
    return (
      <p role="status" className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        {loadingMessage}
      </p>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4"
      >
        <div className="flex items-start gap-2 text-sm text-muted-foreground">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          <p>{errorMessage}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw aria-hidden="true" />
          Retry
        </Button>
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center">
        <p className="text-sm font-medium">{emptyTitle}</p>
        <p className="mt-1 text-sm text-muted-foreground">{emptyDescription}</p>
      </div>
    );
  }

  return children;
}
