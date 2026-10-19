import { useState } from "react";
import type { ModuleProps } from "./shared";
import { PageHeader, Panel } from "./shared";
import type { ScriptImportResult } from "../services/api";

export default function ScriptStudio({ api, project, notify, onChanged }: ModuleProps) {
  const [script, setScript] = useState("");
  const [sourceFormat, setSourceFormat] = useState("museflow");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ScriptImportResult | null>(null);

  async function analyzeAndApply() {
    if (script.trim().length < 5) {
      notify("Kịch bản cần có ít nhất 5 ký tự.", "error");
      return;
    }
    try {
      const shots = await api.listShots(project.id);
      if (shots.length > 0 && !window.confirm(
        `Project đã có ${shots.length} shot. Tác vụ này sẽ thay thế pipeline records hiện tại (Story Bible, scenes và shots) bằng đề xuất từ kịch bản mới. Script revisions được giữ lại. Tiếp tục?`
      )) return;
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không kiểm tra được trạng thái project.", "error");
      return;
    }

    setBusy(true);
    setResult(null);
    try {
      const response = await api.importScript(project.id, {
        raw_content: script,
        source_format: sourceFormat,
        auto_apply_proposal: true,
      });
      setResult(response);
      onChanged();
      notify("Đã import kịch bản và tạo pipeline shot.", "success");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Import kịch bản thất bại.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M2 · SCRIPT TO SHOT"
        title="Script Studio"
        description="Lưu nguyên văn kịch bản nguồn, phân tích characters / locations / scenes, rồi tạo Story Bible và Shot Board."
      />
      <div className="content-grid content-grid-editor">
        <Panel title="Kịch bản nguồn" description="Nên giữ một kịch bản có cấu trúc cảnh và hành động rõ ràng.">
          <div className="field-row">
            <label className="field">
              <span>Source format</span>
              <select value={sourceFormat} onChange={(event) => setSourceFormat(event.target.value)}>
                <option value="museflow">MuseFlow</option>
                <option value="markdown">Markdown</option>
                <option value="plain_text">Plain text</option>
                <option value="custom">Custom</option>
              </select>
            </label>
            <div className="field field-hint">
              <span>Project output</span>
              <strong>{project.aspect_ratio} · {project.fps} FPS</strong>
            </div>
          </div>
          <label className="field">
            <span>Nội dung kịch bản</span>
            <textarea
              className="script-editor"
              value={script}
              onChange={(event) => setScript(event.target.value)}
              placeholder={"Ví dụ:\n\nSCENE 01 — NGOẠI CẢNH / ĐÊM\nMưa rơi trên con phố vắng. Nhân vật A bước đến cánh cửa, dừng lại và quan sát dấu vết trên tay nắm.\n\nSHOT 01: Wide establishing shot...\nSHOT 02: Close-up vào bàn tay..."}
              spellCheck={false}
            />
          </label>
          <div className="editor-footer">
            <span className="muted-text">{script.length.toLocaleString()} ký tự · {script.trim() ? script.trim().split(/\s+/).length : 0} từ</span>
            <button className="button button-primary" disabled={busy || script.trim().length < 5} onClick={() => { void analyzeAndApply(); }}>
              {busy ? <><span className="spinner spinner-light" /> Đang phân tích…</> : "Phân tích & tạo shots →"}
            </button>
          </div>
          <div className="callout callout-warning">
            <strong>Lưu ý về thao tác apply</strong>
            <p>API hiện tại chỉ trả về số lượng entity và diff summary, không cung cấp endpoint preview JSON của proposal. Nút apply sẽ lưu revision và thay thế Story Bible / scenes / shots hiện tại; hãy sao lưu hoặc duplicate project trước khi thay một pipeline đã có dữ liệu.</p>
          </div>
        </Panel>
        <Panel title="Kết quả phân tích" description="Số liệu từ API sau lần import gần nhất.">
          {!result ? (
            <div className="analysis-placeholder">
              <div className="analysis-symbol">Aa</div>
              <strong>Chưa có kết quả trong phiên này</strong>
              <p>Sau khi apply thành công, số lượng characters, locations, scenes và shots sẽ xuất hiện ở đây.</p>
            </div>
          ) : (
            <div className="module-stack">
              <div className="result-summary">
                <span className="success-mark">✓</span>
                <div><strong>Import thành công</strong><small>Revision #{result.version_number} · {result.script_revision_id}</small></div>
              </div>
              <div className="stat-grid stat-grid-2">
                <div className="stat-card"><span className="stat-label">Characters</span><strong>{result.detected_characters}</strong></div>
                <div className="stat-card"><span className="stat-label">Locations</span><strong>{result.detected_locations}</strong></div>
                <div className="stat-card"><span className="stat-label">Scenes</span><strong>{result.detected_scenes}</strong></div>
                <div className="stat-card"><span className="stat-label">Shots created</span><strong>{result.generated_shots}</strong></div>
              </div>
              <div className="callout callout-info">
                <strong>Diff summary</strong>
                <p>{result.diff_summary || "Không có mô tả thay đổi từ parser."}</p>
              </div>
              <div className="detail-list">
                <div><span>Raw source preserved</span><strong>{result.raw_content_preserved ? "Có" : "Không"}</strong></div>
                <div><span>Revision ID</span><strong className="truncate">{result.script_revision_id}</strong></div>
              </div>
              <button className="button button-secondary full-width" onClick={onChanged}>Mở dữ liệu mới trong các module</button>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
