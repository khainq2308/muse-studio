import type { ReactNode } from "react";

interface ResourceCardProps {
  icon: ReactNode;
  title: string;
  description: string;
  count: number;
  action?: ReactNode;
  children: ReactNode;
}

export function ResourceCard({
  icon,
  title,
  description,
  count,
  action,
  children,
}: ResourceCardProps) {
  return (
    <article className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-sm">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
            {icon}
          </div>
          <div>
            <h2 className="font-semibold">{title}</h2>
            <p className="text-sm text-muted-foreground">{description}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-secondary px-2.5 py-1 text-xs font-medium tabular-nums">
            {count}
          </span>
          {action}
        </div>
      </div>
      {children}
    </article>
  );
}
