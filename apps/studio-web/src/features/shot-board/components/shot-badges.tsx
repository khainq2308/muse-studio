import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface StateBadgeProps {
  tone: string;
  children: ReactNode;
  testId?: string;
  title?: string;
  /** `sm` for dense lists such as the shot navigator. */
  size?: "sm" | "md";
}

export function StateBadge({ tone, children, testId, title, size = "md" }: StateBadgeProps) {
  return (
    <span
      data-testid={testId}
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full font-medium",
        size === "sm" ? "px-1.5 py-0.5 text-[11px] leading-4" : "px-2.5 py-1 text-xs",
        tone,
      )}
    >
      {children}
    </span>
  );
}
