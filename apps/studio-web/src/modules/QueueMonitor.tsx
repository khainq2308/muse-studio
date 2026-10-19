import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { Connection, QueueTask, Shot } from "../services/api";
import type { ModuleProps } from "./shared";
import { EmptyState, ErrorState, LoadingLine, PageHeader, Panel, StatusBadge } from "./shared";

export default function QueueMonitor({ api, project, refreshToken, notify, onChanged }: ModuleProps) {
  const [tasks, setTasks] = useState<QueueTask[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [launch, setLaunch] = useState({ shot_id: "", connection_id: "", task_type: "video" as "image" | "video", model_id: "muse-video-mock-v1", priority: 10 });

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextTasks, nextShots, nextConnections] = await Promise.all([
        api.listTasks(project.id),
        api.listShots(project.id),
        api.listConnections(),
      ]);
      setTasks(nextTasks);
      setShots(nextShots);
      setConnections(nextConnections);
      setLaunch((current) => ({
        ...current,
        shot_id: nextShots.some((shot) => shot.id === current.shot_id) ? current.shot_id : (nextShots[0]?.id ?? ""),
        connection_id: nextConnections.some((connection) => connection.id === current.connection_id && connection.enabled)
          ? current.connection_id
          : (nextConnections.find((connection) => connection.enabled)?.id ?? ""),
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được queue.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [project.id, refreshToken]);

  const visibleTasks = useMemo(() => {
    const sorted = [...tasks].sort((a, b) => {
      const at = a.state === "running" ? 0 : a.state === "queued" ? 1 : 2;
      const bt = b.state === "running" ? 0 : b.state === "queued" ? 1 : 2;
      return at - bt || b.priority - a.priority;
    });
    return stateFilter === "all" ? sorted : sorted.filter((task) => task.state === stateFilter);
  }, [tasks, stateFilter]);

  const counts = tasks.reduce<Record<string, number>>((sum, task) => {
    sum[task.state] = (sum[task.state] ?? 0) + 1;
    return sum;
  }, {});
  const enabledConnections = connections.filter((connection) => connection.enabled);

  async function enqueue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!launch.shot_id || !launch.connection_id) {
      notify("Cần chọn shot và connection đã bật.", "error");
      return;
    }
    setBusy(true);
    try {
      const result = await api.enqueueTask({
        shot_id: launch.shot_id,
        connection_id: launch.connection_id,
        task_type: launch.task_type,
        model_id: launch.model_id.trim(),
        priority: launch.priority,
        max_polls: 60,
      });
      notify(result.created_new === false ? "Task idempotent đã tồn tại, không tạo task trùng." : "Đã tạo task và gửi vào worker queue.", "success");
      await load();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không enqueue được task.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function runAction(task: QueueTask, action: "pause" | "resume" | "cancel") {
    if (action === "cancel" && !window.confirm(`Hủy task ${task.id}?`)) return;
    setBusy(true);
    try {
      await api.taskAction(task.id, action);
      notify(`Đã gửi lệnh ${action} cho task.`, "success");
      await load();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không thực hiện được thao tác task.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M4 · GENERATION QUEUE"
        title="Queue Monitor"
        description="Tạo generation task, xem tiến trình poll/backoff và thao tác pause, resume hoặc cancel."
        actions={<button className="button button-secondary" onClick={() => { void load(); }}>↻ Refresh</button>}
      />
      <div className="stat-grid stat-grid-5">
        <div className="stat-card"><span className="stat-label">Tổng task</span><strong>{tasks.length}</strong></div>
        <div className="stat-card"><span className="stat-label">Queued</span><strong>{counts.queued ?? 0}</strong></div>
        <div className="stat-card"><span className="stat-label">Running</span><strong>{counts.running ?? 0}</strong></div>
        <div className="stat-card"><span className="stat-label">Succeeded</span><strong>{counts.succeeded ?? 0}</strong></div>
        <div className="stat-card"><span className="stat-label">Failed</span><strong>{(counts.failed ?? 0) + (counts.poll_timeout ?? 0)}</strong></div>
      </div>
      <div className="content-grid content-grid-wide">
        <Panel title="Tạo generation task" description="Worker ARQ sẽ dispatch/poll task sau khi record được lưu.">
          {enabledConnections.length === 0 || shots.length === 0 ? (
            <EmptyState title={enabledConnections.length === 0 ? "Chưa có connection khả dụng" : "Chưa có shot"} detail={enabledConnections.length === 0 ? "Tạo và bật một connection trong Connections & Accounts." : "Import kịch bản trong Script Studio để tạo shot."} />
          ) : (
            <form className="form-stack" onSubmit={(event) => { void enqueue(event); }}>
              <label className="field"><span>Shot</span><select required value={launch.shot_id} onChange={(event) => setLaunch({ ...launch, shot_id: event.target.value })}>{shots.map((shot) => <option value={shot.id} key={shot.id}>{shot.sequence_index}. {shot.title} · {shot.id}</option>)}</select></label>
              <label className="field"><span>Connection</span><select required value={launch.connection_id} onChange={(event) => setLaunch({ ...launch, connection_id: event.target.value })}>{enabledConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name} · {connection.provider_type}</option>)}</select></label>
              <div className="field-grid-2">
                <label className="field"><span>Task type</span><select value={launch.task_type} onChange={(event) => setLaunch({ ...launch, task_type: event.target.value as "image" | "video" })}><option value="video">Video</option><option value="image">Image</option></select></label>
                <label className="field"><span>Priority (1–100)</span><input type="number" min={1} max={100} value={launch.priority} onChange={(event) => setLaunch({ ...launch, priority: Number(event.target.value) })} /></label>
              </div>
              <label className="field"><span>Model ID</span><input required value={launch.model_id} onChange={(event) => setLaunch({ ...launch, model_id: event.target.value })} /></label>
              <button className="button button-primary full-width" disabled={busy || !launch.model_id.trim()}>{busy ? "Đang enqueue…" : "＋ Add to queue"}</button>
            </form>
          )}
          <div className="callout callout-info"><strong>Production note</strong><p>Generation tiêu thụ quota provider. Kiểm tra connection/account pool và model ID thực tế trước khi chạy task thật.</p></div>
        </Panel>
        <Panel title="Task activity" description="Trạng thái được lưu từ worker; refresh để nhận diễn biến mới nhất." action={
          <label className="field field-inline"><span>Filter</span><select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">All states</option>{["queued","running","paused","succeeded","failed","canceled","poll_timeout"].map((state) => <option value={state} key={state}>{state}</option>)}</select></label>
        }>
          {loading ? <LoadingLine /> : error ? <ErrorState message={error} onRetry={() => { void load(); }} /> : visibleTasks.length === 0 ? (
            <EmptyState title="Không có task" detail="Tạo task bằng biểu mẫu bên trái để bắt đầu theo dõi generation." />
          ) : (
            <div className="table-wrap">
              <table className="data-table queue-table">
                <thead><tr><th>Task</th><th>State</th><th>Progress</th><th>Account / error</th><th>Actions</th></tr></thead>
                <tbody>
                  {visibleTasks.map((task) => (
                    <tr key={task.id}>
                      <td><strong className="truncate">{task.id}</strong><small>Shot {task.shot_id}</small><small>{task.model_id}</small></td>
                      <td><StatusBadge status={task.state} /></td>
                      <td><div className="poll-meter"><span style={{ width: `${Math.min(100, task.max_polls ? task.poll_count / task.max_polls * 100 : 0)}%` }} /></div><small>{task.poll_count}/{task.max_polls} polls</small><small>Next delay {task.next_poll_delay_seconds}s</small></td>
                      <td><span>{task.account_used || "—"}</span>{task.error_category && <small className="error-text">{task.error_category}{task.retryable ? " · retryable" : ""}</small>}{task.error_message_redacted && <small title={task.error_message_redacted}>{task.error_message_redacted}</small>}</td>
                      <td><div className="action-stack">
                        {["queued","running"].includes(task.state) && <button disabled={busy} className="button button-quiet button-small" onClick={() => { void runAction(task, "pause"); }}>Pause</button>}
                        {task.state === "paused" && <button disabled={busy} className="button button-quiet button-small" onClick={() => { void runAction(task, "resume"); }}>Resume</button>}
                        {["queued","running","paused"].includes(task.state) && <button disabled={busy} className="button button-danger button-small" onClick={() => { void runAction(task, "cancel"); }}>Cancel</button>}
                        {task.take_id && <small>Take {task.take_id}</small>}
                      </div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
