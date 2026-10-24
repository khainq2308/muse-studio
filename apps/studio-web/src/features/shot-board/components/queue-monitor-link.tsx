import { Link } from "@tanstack/react-router";
import { ListChecks } from "lucide-react";

/** Route link to this project's Queue Monitor, where generation progress is tracked. */
export function QueueMonitorLink({
  projectId,
  label = "Open Generation Queue",
}: {
  projectId: string;
  label?: string;
}) {
  return (
    <Link
      to="/projects/$projectId/$module"
      params={{ projectId, module: "queue-monitor" }}
      className="inline-flex items-center gap-1 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ListChecks className="size-4" aria-hidden="true" />
      {label}
    </Link>
  );
}
