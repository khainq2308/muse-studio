import type { ProjectShot } from "@/entities/shot/api";

type OrderableShot = Pick<ProjectShot, "id" | "scene_id" | "sequence_index">;

/**
 * Shots grouped by scene: scenes in order of their first shot, shots by sequence index. This is
 * the Shot Board navigator order, shared so other views point at "the first" shot consistently.
 */
export function groupShotsByScene<T extends OrderableShot>(
  shots: readonly T[],
): { sceneId: string; shots: T[] }[] {
  const ordered = [...shots].sort(
    (left, right) => left.sequence_index - right.sequence_index || left.id.localeCompare(right.id),
  );
  const groups = new Map<string, T[]>();
  for (const shot of ordered) {
    const group = groups.get(shot.scene_id);
    if (group) group.push(shot);
    else groups.set(shot.scene_id, [shot]);
  }
  return [...groups.entries()].map(([sceneId, sceneShots]) => ({ sceneId, shots: sceneShots }));
}

/** Shots in navigator order. */
export function navigatorOrder<T extends OrderableShot>(shots: readonly T[]): T[] {
  return groupShotsByScene(shots).flatMap((group) => group.shots);
}
