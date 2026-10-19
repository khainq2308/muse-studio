import { useEffect, useState } from "react";
import { ApiError } from "../services/api";
import type { ExportResult, TimelineInfo } from "../services/api";
import type { ModuleProps } from "./shared";
import { Badge, EmptyState, ErrorState, LoadingLine, PageHeader, Panel, StatusBadge } from "./shared";

export default function Timeline({ api, project, refreshToken, notify }: ModuleProps) {
  const [timeline, setTimeline] = useState<TimelineInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [transition, setTransition] = useState<"hard_cut" | "cross_dissolve">("hard_cut");
  const [transitionDuration, setTransitionDuration] = useState(0.5);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [timelineName, setTimelineName] = useState("Master Cut v1");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setTimeline(await api.getTimeline(project.id));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setTimeline(null);
      } else {
        setError(err instanceof Error ? err.message : "Không tải được timeline.");
      }
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [project.id, refreshToken]);

  async function assemble() {
    setBusy(true);
    setError("");
    setExportResult(null);
    try {
      const result = await api.assembleTimeline(project.id, {
        name: timelineName.trim() || "Master Cut v1",
        allow_missing_shots: true,
        default_transition: transition,
        transition_duration_seconds: transition === "cross_dissolve" ? transitionDuration : 0,
      });
      setTimeline(result);
      notify("Đã assemble timeline từ các selected takes.", "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Không assemble được timeline.";
      setError(message);
      notify(message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function exportVideo() {
    if (!timeline) return;
    if (!window.confirm("Bắt đầu export MP4 bằng FFmpeg? Việc này có thể tiêu thụ CPU và disk I/O đáng kể.")) return;
    setBusy(true);
    setError("");
    setExportResult(null);
    try {
      const result = await api.exportTimeline(project.id, timeline.sequence_id);
      setExportResult(result);
      if (result.state === "succeeded") {
        notify("Export MP4 thành công và đã lưu asset.", "success");
      } else {
        notify(`Export kết thúc với trạng thái ${result.state}.`, "info");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Export timeline thất bại.";
      setError(message);
      notify(message, "error");
    } finally {
      setBusy(false);
    }
  }

  const unsupported = exportResult?.unsupported_features ?? [];
  const manifest = timeline?.manifest ?? {};
  const videoClips = Array.isArray(manifest.video_clips) ? manifest.video_clips.length : 0;
  const audioTracks = Array.isArray(manifest.audio_tracks) ? manifest.audio_tracks.length : 0;

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M6 · TIMELINE & EXPORT"
        title="Timeline Editor"
        description="Assemble selected takes theo sequence, kiểm tra cảnh báo và render MP4 bằng FFmpeg/FFprobe."
        actions={<button className="button button-secondary" onClick={() => { void load(); }}>↻ Load timeline</button>}
      />
      {error && <ErrorState message={error} onRetry={() => { void load(); }} />}
      <div className="content-grid content-grid-editor">
        <Panel title="Assemble timeline" description="API hiện assemble sequence từ selected takes và tùy chọn transition cơ bản.">
          <div className="form-stack">
            <label className="field"><span>Sequence name</span><input value={timelineName} onChange={(event) => setTimelineName(event.target.value)} /></label>
            <label className="field"><span>Default transition</span><select value={transition} onChange={(event) => setTransition(event.target.value as "hard_cut" | "cross_dissolve")}><option value="hard_cut">Hard cut</option><option value="cross_dissolve">Cross dissolve</option></select></label>
            {transition === "cross_dissolve" && <label className="field"><span>Transition duration (seconds)</span><input type="number" min={0} max={3} step={0.1} value={transitionDuration} onChange={(event) => setTransitionDuration(Number(event.target.value))} /></label>}
            <div className="callout callout-info"><strong>Missing takes</strong><p>Assemble cho phép bỏ qua shot chưa có selected take; các thiếu hụt sẽ được đưa vào warnings của timeline.</p></div>
            <button className="button button-primary full-width" disabled={busy} onClick={() => { void assemble(); }}>{busy ? "Đang xử lý…" : "Assemble / tạo sequence"}</button>
          </div>
        </Panel>
        <Panel title="Current sequence" description="Thông tin đã được lưu trong timeline manifest.">
          {loading ? <LoadingLine /> : !timeline ? (
            <EmptyState title="Chưa có timeline" detail="Assemble một sequence để bắt đầu kiểm tra timeline và export." />
          ) : (
            <div className="module-stack">
              <div className="timeline-title-row"><div><h3>{timeline.name}</h3><small className="muted-text">{timeline.sequence_id}</small></div><Badge tone="good">Saved</Badge></div>
              <div className="stat-grid stat-grid-2">
                <div className="stat-card"><span className="stat-label">Duration</span><strong>{timeline.total_duration_seconds.toFixed(1)}s</strong></div>
                <div className="stat-card"><span className="stat-label">Video clips</span><strong>{videoClips}</strong></div>
                <div className="stat-card"><span className="stat-label">Audio tracks</span><strong>{audioTracks}</strong></div>
                <div className="stat-card"><span className="stat-label">Output</span><strong>{timeline.resolution}</strong><small>{timeline.aspect_ratio} · {timeline.fps} FPS</small></div>
              </div>
              {(timeline.warnings ?? []).length > 0 && <div className="callout callout-warning"><strong>Timeline warnings</strong><pre>{JSON.stringify(timeline.warnings, null, 2)}</pre></div>}
              <button className="button button-primary full-width" disabled={busy} onClick={() => { void exportVideo(); }}>Render MP4 (FFmpeg)</button>
            </div>
          )}
        </Panel>
      </div>

      <Panel title="Export job" description="Mỗi export dùng FFprobe để xác minh file trước khi asset được tạo.">
        {!exportResult ? <EmptyState title="Chưa có export trong phiên này" detail="Render sequence để xem trạng thái job và link tải MP4." /> : (
          <div className="module-stack">
            <div className="export-result-head"><div><strong>Export {exportResult.export_id}</strong><small>Output asset {exportResult.output_asset_id ?? "—"}</small></div><StatusBadge status={exportResult.state} /></div>
            {unsupported.length > 0 && <div className="callout callout-warning"><strong>Các feature chưa được render bởi exporter</strong><p>{unsupported.map((feature) => feature.replace(/_/g, " ")).join(" · ")}</p><small>Exporter hiện chưa mix clip audio, title cards, subtitles hoặc extra audio tracks nếu các feature đó được dùng. UI báo rõ để không nhầm với kết quả đã render.</small></div>}
            {exportResult.output_asset_id && <a className="button button-secondary" href={api.assetDownloadUrl(exportResult.output_asset_id)} target="_blank" rel="noreferrer">↓ Tải file MP4</a>}
            {exportResult.ffprobe_verification && <details className="json-details"><summary>FFprobe verification details</summary><pre>{JSON.stringify(exportResult.ffprobe_verification, null, 2)}</pre></details>}
          </div>
        )}
      </Panel>
    </div>
  );
}
