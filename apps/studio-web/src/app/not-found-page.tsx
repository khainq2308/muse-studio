import { Link } from "@tanstack/react-router";

interface NotFoundPageProps {
  title?: string;
  message?: string;
  /** Use 2 when rendered inside the app shell, which already owns the page's h1. */
  headingLevel?: 1 | 2;
}

/** Shared 404 content for unknown URLs, unknown project modules and missing projects. */
export function NotFoundPage({
  title = "404 - Page not found",
  message = "The page you requested doesn't exist.",
  headingLevel = 1,
}: NotFoundPageProps) {
  const Heading = headingLevel === 1 ? "h1" : "h2";
  return (
    <div data-testid="not-found-page" className="flex flex-col gap-3">
      <Heading className="text-2xl font-semibold">{title}</Heading>
      <p className="text-muted-foreground">{message}</p>
      <Link
        to="/"
        className="text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Back to workspace overview
      </Link>
    </div>
  );
}

export default NotFoundPage;
