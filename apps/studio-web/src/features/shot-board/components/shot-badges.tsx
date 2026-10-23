import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface StateBadgeProps {
  tone: string;
  children: ReactNode;
  testId?: string;
  title?: string;
}

export function StateBadge({ tone, children, testId, title }: StateBadgeProps) {
  return (
    <span
      data-testid={testId}
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium",
        tone,
      )}
    >
      {children}
    </span>
  );
}
