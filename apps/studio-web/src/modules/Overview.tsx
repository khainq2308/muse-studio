import { useEffect, useState } from "react";
import type { ModuleProps } from "./shared";
import { EmptyState, LoadingLine, PageHeader, Panel, StatusBadge } from "./shared";
import type { Connection, QueueTask, Shot } from "../services/api";

export default function Overview({ api, project, refreshToken, notify, onChanged }: ModuleProps) {
  const [shots, setShots] = useState<Shot[]>([]);
  const [tasks, setTasks] = useState<QueueTask[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [bibleCount, setBibleCount] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true);
    setError("");
    try {
      const [nextShots, nextTasks, nextConnections, bible] = await Promise.all([
        api.listShots(project.id),
        api.listTasks(project.id),
        api.listConnections(),
        api.listBible(project.id),
      ]);
      setShots(nextShots);
      setTasks(nextTasks);
      setConnections(nextConnections);
      setBibleCount(bible.length);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Không tải được dashboard.";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void load(); }, [project.id, refreshToken]);

  const finished = tasks.filter((task) => task.state === "succeeded").length;
  const running = tasks.filter((task) => task.state === "running" || task.state === "queued").length;
  const failed = tasks.filter((task) => task.state === "failed" || task.state === "poll_timeout").length;
  const approved = shots.filter((shot) => shot.approval_state === "approved").length;
  const blocked = shots.filter((shot) => shot.generation_state === "blocked_reference" || shot.generation_state === "blocked_upstream_frame").length;

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="WORKSPACE OVERVIEW"
        title={`Chào mừng đến với ${project.name}`}
        description="Một nơi để biến kịch bản thành shot, theo dõi generation và chuẩn bị timeline xuất phim."
        actions={<button className="button button-secondary" onClick={() => { void load(); }}>↻ Làm mới</button>}
      />
      {error && <div className="inline-error"><span>{error}</span><button className="button button-small button-quiet" onClick={() => { void load(); }}>Thử lại</button></div>}
      {busy ? <LoadingLine label="Đang đồng bộ trạng thái project…" /> : (
        <>
          <div className="stat-grid">
            <article className="stat-card stat-card-accent">
              <span className="stat-label">Shots</span>
              <strong>{shots.length}</strong>
              <small>{approved} đã duyệt · {blocked} bị block</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Generation queue</span>
              <strong>{running}</strong>
              <small>đang chạy hoặc chờ xử lý</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Kết quả thành công</span>
              <strong>{finished}</strong>
              <small>{failed} task lỗi hoặc hết lượt poll</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">Story Bible</span>
              <strong>{bibleCount}</strong>
              <small>{connections.filter((connection) => connection.enabled).length} kết nối đang bật</small>
            </article>
          </div>
          <div className="content-grid content-grid-wide">
            <Panel title="Generation mới nhất" description="Các task của project được lưu trong database.">
              {tasks.length === 0 ? <EmptyState title="Queue đang trống" detail="Chọn một shot trong Shot Board hoặc khởi chạy batch render qua API để tạo task đầu tiên." /> : (
                <div className="table-wrap">
                  <table className="data-table">
                    <thead><tr><th>Task / Shot</th><th>Model</th><th>Trạng thái</th><th>Poll</th></tr></thead>
                    <tbody>
                      {tasks.slice(0, 6).map((task) => (
                        <tr key={task.id}>
                          <td><strong>{task.id}</strong><small>{task.shot_id}</small></td>
                          <td>{task.model_id}</td>
                          <td><StatusBadge status={task.state} /></td>
                          <td>{task.poll_count}/{task.max_polls}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            <Panel title="Project hiện tại" description="Thông số dùng khi tạo shot và dựng timeline.">
              <div className="detail-list">
                <div><span>Aspect ratio</span><strong>{project.aspect_ratio}</strong></div>
                <div><span>Frame rate</span><strong>{project.fps} FPS</strong></div>
                <div><span>Style preset</span><strong>{project.style_preset}</strong></div>
                <div><span>Output profile</span><strong>{project.output_profile}</strong></div>
                <div><span>Revision</span><strong>#{project.current_revision_num}</strong></div>
              </div>
              <div className="callout callout-info">
                <strong>Quy trình khuyến nghị</strong>
                <p>Import kịch bản → rà Story Bible → kiểm tra lint/duyệt shot → chạy generation → chọn take → assemble và export timeline.</p>
              </div>
              <button className="button button-primary full-width" onClick={() => { onChanged(); notify("Dữ liệu workspace đã được làm mới.", "success"); }}>Đồng bộ workspace</button>
            </Panel>
          </div>
          <Panel title="Provider connections" description="Trạng thái hiện tại từ Orchestrator.">
            {connections.length === 0 ? <EmptyState title="Chưa có connection" detail="Tạo kết nối muse2api hoặc mock trong Connections & Accounts trước khi gửi job." /> : (
              <div className="connection-mini-grid">
                {connections.map((connection) => (
                  <div className="connection-mini" key={connection.id}>
                    <span className="connection-icon">{connection.provider_type === "mock" ? "◈" : "↗"}</span>
                    <div className="connection-mini-copy">
                      <strong>{connection.name}</strong>
                      <small>{connection.provider_type} · concurrency {connection.max_concurrency}</small>
                    </div>
                    <StatusBadge status={connection.enabled ? connection.health_state : "disabled"} />
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}
