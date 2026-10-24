import { KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/lib/stores/session-store";

export function AdminTokenForm() {
  const { t } = useTranslation("admin-session");
  const [draftAdminToken, setDraftAdminToken] = useState("");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const setAdminToken = useSessionStore((state) => state.setAdminToken);
  const clearAdminToken = useSessionStore((state) => state.clearAdminToken);
  const canConnect = draftAdminToken.trim().length > 0;

  return (
    <form
      className="flex flex-col gap-2 sm:flex-row sm:items-center"
      onSubmit={(event) => {
        event.preventDefault();
        // An empty submission must never silently disconnect; Disconnect is the explicit path.
        if (!canConnect) return;
        setAdminToken(draftAdminToken);
        setDraftAdminToken("");
      }}
    >
      <div className="relative flex items-center">
        {activeAdminToken ? (
          <ShieldCheck
            className="pointer-events-none absolute left-2.5 size-3.5 text-success"
            aria-hidden="true"
          />
        ) : (
          <KeyRound
            className="pointer-events-none absolute left-2.5 size-3.5 text-muted-foreground"
            aria-hidden="true"
          />
        )}
        <input
          aria-label={t("ariaLabel")}
          type="password"
          autoComplete="off"
          placeholder={activeAdminToken ? t("placeholderActive") : t("placeholderEmpty")}
          value={draftAdminToken}
          onChange={(event) => setDraftAdminToken(event.target.value)}
          className="h-9 min-w-0 rounded-lg border border-input bg-background pl-8 pr-3 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring sm:w-52"
        />
      </div>
      <div className="flex items-center gap-1.5">
        <Button type="submit" size="sm" disabled={!canConnect}>
          {activeAdminToken ? t("updateToken") : t("connect")}
        </Button>
        {activeAdminToken && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setDraftAdminToken("");
              clearAdminToken();
            }}
          >
            {t("disconnect")}
          </Button>
        )}
      </div>
    </form>
  );
}
