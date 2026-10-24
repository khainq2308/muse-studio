import { parseScriptStudioView, type ScriptStudioView } from "@/features/script-studio/search";
import { validateShotBoardSearch, type ShotBoardSearch } from "@/features/shot-board/search";

/** Optional per-module URL state of `/projects/$projectId/$module`. */
export interface ProjectModuleSearch extends ShotBoardSearch {
  /** Script & Story Bible view (`?view=script|bible`). */
  view?: ScriptStudioView;
}

/**
 * Route `validateSearch`: each module's keys are validated by that module. Every key is always
 * returned (possibly `undefined`) because the router merges the result over the raw search.
 */
export function validateProjectModuleSearch(search: Record<string, unknown>): ProjectModuleSearch {
  return { ...validateShotBoardSearch(search), view: parseScriptStudioView(search.view) };
}
