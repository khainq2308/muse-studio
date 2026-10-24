import type { ReactNode } from "react";

interface PageHeaderProps {
  /** Page title; matches the navigation label of the page. */
  title: string;
  /** One or two plain-language sentences on what the page is for. */
  description?: ReactNode;
  /** Optional status shown next to the title (e.g. "Simulation"). */
  badge?: ReactNode;
  /** Primary actions, right-aligned on wide screens. */
  actions?: ReactNode;
  /** Id for `aria-labelledby` on the page section. */
  headingId?: string;
}

/**
 * The one page header used by every workspace page and project module, so titles, spacing and
 * action placement are the same everywhere.
 */
export function PageHeader({ title, description, badge, actions, headingId }: PageHeaderProps) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={headingId} className="text-xl font-semibold tracking-tight">
            {title}
          </h2>
          {badge}
        </div>
        {description && (
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
