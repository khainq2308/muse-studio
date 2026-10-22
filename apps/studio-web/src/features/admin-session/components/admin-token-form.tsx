import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/lib/stores/session-store";

export function AdminTokenForm() {
  const [draftAdminToken, setDraftAdminToken] = useState("");
  const activeAdminToken = useSessionStore((state) => state.adminToken);
  const setAdminToken = useSessionStore((state) => state.setAdminToken);
  const clearAdminToken = useSessionStore((state) => state.clearAdminToken);
  const canConnect = draftAdminToken.trim().length > 0;

  return (
    <form
      className="flex flex-col gap-2 sm:flex-row"
      onSubmit={(event) => {
        event.preventDefault();
        // An empty submission must never silently disconnect; Disconnect is the explicit path.
        if (!canConnect) return;
        setAdminToken(draftAdminToken);
        setDraftAdminToken("");
      }}
    >
      <input
        aria-label="Studio admin token"
        type="password"
        autoComplete="off"
        placeholder="Studio admin token"
        value={draftAdminToken}
        onChange={(event) => setDraftAdminToken(event.target.value)}
        className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring sm:w-64"
      />
      <Button type="submit" disabled={!canConnect}>
        {activeAdminToken ? "Update token" : "Connect"}
      </Button>
      {activeAdminToken && (
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setDraftAdminToken("");
            clearAdminToken();
          }}
        >
          Disconnect
        </Button>
      )}
    </form>
  );
}
