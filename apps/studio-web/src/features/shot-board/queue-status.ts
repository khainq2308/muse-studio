import type { QueueTask, QueueTaskPage } from "@/entities/task/state";

/**
 * Each shot's newest task among `tasks`, which the API returns newest first. Shots whose last
 * task is older than the loaded page have no entry: "unknown", never "no task".
 */
export function latestTaskByShot(tasks: readonly QueueTask[]): Map<string, QueueTask> {
  const latest = new Map<string, QueueTask>();
  for (const task of tasks) {
    if (task.shot_id && !latest.has(task.shot_id)) latest.set(task.shot_id, task);
  }
  return latest;
}

export interface GenerationActivity {
  /** Queued, running or awaiting input, across the whole project (backend summary). */
  active: number;
  awaitingInput: number;
  /** Shots whose latest task failed or timed out. Older failures already retried don't count. */
  failedShots: number;
}

export function summarizeGenerationActivity(
  page: Pick<QueueTaskPage, "summary">,
  latest: ReadonlyMap<string, QueueTask>,
): GenerationActivity {
  let failedShots = 0;
  for (const task of latest.values()) {
    if (task.state === "failed" || task.state === "poll_timeout") failedShots += 1;
  }
  return {
    active: page.summary.active_tasks,
    awaitingInput: page.summary.awaiting_input_tasks,
    failedShots,
  };
}
