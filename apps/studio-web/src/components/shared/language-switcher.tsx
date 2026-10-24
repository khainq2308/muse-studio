import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { changeLanguage } from "@/lib/i18n";
import type { AppLanguage } from "@/lib/i18n/storage";

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation();
  const currentLang = (i18n.language || "vi").startsWith("en") ? "en" : "vi";
  const targetLang: AppLanguage = currentLang === "vi" ? "en" : "vi";

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      data-testid="language-switcher"
      aria-label={t("language.label", { defaultValue: "Language" })}
      title={
        currentLang === "vi"
          ? "Chuyển sang tiếng Anh (English)"
          : "Switch to Vietnamese (Tiếng Việt)"
      }
      onClick={() => void changeLanguage(targetLang)}
      className="h-9 gap-1.5 px-2.5 text-xs font-semibold"
    >
      <Languages className="size-3.5 text-muted-foreground" aria-hidden="true" />
      <span className="uppercase">{currentLang}</span>
    </Button>
  );
}
