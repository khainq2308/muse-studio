import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

interface NotFoundPageProps {
  title?: string;
  message?: string;
  /** Use 2 when rendered inside the app shell, which already owns the page's h1. */
  headingLevel?: 1 | 2;
}

/** Shared 404 content for unknown URLs, unknown project modules and missing projects. */
export function NotFoundPage({ title, message, headingLevel = 1 }: NotFoundPageProps) {
  const { t } = useTranslation();
  const Heading = headingLevel === 1 ? "h1" : "h2";
  const displayTitle = title ?? t("notFound.title");
  const displayMessage = message ?? t("notFound.description");

  return (
    <div data-testid="not-found-page" className="flex flex-col gap-3">
      <Heading className="text-2xl font-semibold">{displayTitle}</Heading>
      <p className="text-muted-foreground">{displayMessage}</p>
      <Link
        to="/"
        className="text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {t("notFound.returnHome")}
      </Link>
    </div>
  );
}

export default NotFoundPage;
