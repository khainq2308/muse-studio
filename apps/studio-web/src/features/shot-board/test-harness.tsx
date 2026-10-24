import { useState } from "react";
import {
  ShotBoardPage,
  type ShotBoardNavigateOptions,
} from "@/features/shot-board/pages/shot-board-page";
import type { ShotBoardSearch } from "@/features/shot-board/search";

/**
 * Test-only stand-in for the route: holds the `?shot=&tab=` state the router would keep in the
 * URL and reports every navigation the page requests.
 */
export function ShotBoardHarness({
  projectId,
  initialSearch = {},
  onNavigate,
}: {
  projectId: string;
  initialSearch?: ShotBoardSearch;
  onNavigate?: (search: ShotBoardSearch, options?: ShotBoardNavigateOptions) => void;
}) {
  const [search, setSearch] = useState<ShotBoardSearch>(initialSearch);
  return (
    <ShotBoardPage
      projectId={projectId}
      search={search}
      onSearchChange={(next, options) => {
        onNavigate?.(next, options);
        setSearch(next);
      }}
    />
  );
}
