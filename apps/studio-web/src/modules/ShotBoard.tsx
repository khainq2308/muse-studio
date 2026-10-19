import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { ApprovalState, Shot, ShotTake } from "../services/api";
import type { ModuleProps } from "./shared";
import { Badge, EmptyState, ErrorState, LoadingLine, PageHeader, Panel, StatusBadge } from "./shared";

interface EditForm {
  shot_size: string;
  camera_motion: string;
  composition: string;
  action: string;
  duration_seconds: number;
  continuity_mode: string;
  approval_state: ApprovalState;
}

function toEditForm(shot: Shot): EditForm {
  return {
    shot_size: shot.shot_size ?? "",
    camera_motion: shot.camera_motion ?? "",
    composition: shot.composition ?? "",
    action: shot.action ?? "",
    duration_seconds: shot.duration_seconds ?? 5,
    continuity_mode: shot.continuity_mode ?? "none",
    approval_state: shot.approval_state ?? "needs_review",
  };
}

export default function ShotBoard({ api, project, refreshToken, notify, onChanged }: ModuleProps) {
  const [shots, setShots] = useState<Shot[]>([]);
  const [takes, setTakes] = useState<ShotTake[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [edit, setEdit] = useState<EditForm | null>(null);
  const [loading, setLoading] = useState(true);
  const [takesLoading, setTakesLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const rows = await api.listShots(project.id);
      setShots(rows);
      const stillExists = rows.some((shot) => shot.id === selectedId);
      const nextId = stillExists ? selectedId : (rows[0]?.id ?? "");
      setSelectedId(nextId);
      const current = rows.find((shot) => shot.id === nextId);
      setEdit(current ? toEditForm(current) : null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được Shot Board.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [project.id, refreshToken]);

  const selected = shots.find((shot) => shot.id === selectedId) ?? null;
  useEffect(() => {
    if (!selectedId) {
      setTakes([]);
      return;
    }
    let cancelled = false;
    setTakesLoading(true);
    api.listTakes(selectedId).then((rows) => {
      if (!cancelled) setTakes(rows);
    }).catch((err: unknown) => {
      if (!cancelled) {
        setTakes([]);
        notify(err instanceof Error ? err.message : "Không tải được takes của shot.", "error");
      }
    }).finally(() => {
      if (!cancelled) setTakesLoading(false);
    });
    return () => { cancelled = true; };
  }, [api, selectedId, refreshToken]);

  const filtered = useMemo(() => shots.filter((shot) => {
    const matchesFilter = filter === "all" || (filter === "blocked"
      ? shot.generation_state === "blocked_reference" || shot.generation_state === "blocked_upstream_frame"
      : filter === "needs_review" ? shot.approval_state !== "approved" : shot.generation_state === filter);
    const q = query.trim().toLowerCase();
    const matchesQuery = !q || [shot.title, shot.id, shot.action, shot.location_code, ...(shot.subject_codes ?? [])]
      .some((value) => String(value ?? "").toLowerCase().includes(q));
    return matchesFilter && matchesQuery;
  }), [shots, filter, query]);

  function selectShot(shot: Shot) {
    setSelectedId(shot.id);
    setEdit(toEditForm(shot));
  }

  async function saveShot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !edit) return;
    setSaving(true);
    try {
      const result = await api.updateShot(project.id, selected.id, edit);
      notify("Shot đã lưu; prompt và lint đã được cập nhật.", "success");
      await load();
      setEdit({ ...edit, approval_state: result.approval_state });
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không lưu được shot.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function chooseTake(take: ShotTake) {
    if (!selected) return;
    try {
      await api.selectTake(selected.id, take.id);
      notify(`Đã chọn Take #${take.take_number}.`, "success");
      await load();
      setTakes(await api.listTakes(selected.id));
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không chọn được take.", "error");
    }
  }

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M2 · SHOT BOARD"
        title="Shot Board"
        description="Rà prompt, xem lỗi continuity/reference, duyệt shot và chọn take cho timeline."
        actions={<button className="button button-secondary" onClick={() => { void load(); }}>↻ Refresh shots</button>}
      />
      <div className="shot-workspace">
        <Panel title="Shots" description={`${shots.length} shots trong project`} className="shot-list-panel">
          <div className="shot-filters">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm shot, action, location…" aria-label="Tìm shot" />
            <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Lọc shots">
              <option value="all">Tất cả trạng thái</option>
              <option value="needs_review">Cần duyệt</option>
              <option value="blocked">Bị block</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
            </select>
          </div>
          {loading ? <LoadingLine /> : error ? <ErrorState message={error} onRetry={() => { void load(); }} /> : filtered.length === 0 ? (
            <EmptyState title="Không có shot phù hợp" detail="Import kịch bản trong Script Studio hoặc điều chỉnh bộ lọc." />
          ) : (
            <div className="shot-list">
              {filtered.map((shot) => (
                <button key={shot.id} className={`shot-list-item ${selectedId === shot.id ? "is-selected" : ""}`} onClick={() => selectShot(shot)}>
                  <div className="shot-list-top"><span className="shot-index">{String(shot.sequence_index).padStart(2, "0")}</span><span className="shot-title">{shot.title || "Untitled shot"}</span></div>
                  <small>{shot.location_code || "No location"} · {shot.duration_seconds}s</small>
                  <div className="shot-list-badges"><StatusBadge status={shot.generation_state} /><StatusBadge status={shot.approval_state} /></div>
                </button>
              ))}
            </div>
          )}
        </Panel>

        <div className="shot-detail-column">
          {!selected || !edit ? (
            <Panel><EmptyState title="Chọn một shot" detail="Chi tiết prompt, linter và takes sẽ hiển thị tại đây." /></Panel>
          ) : (
            <>
              <Panel title={selected.title || selected.id} description={`${selected.id} · Scene ${selected.scene_id ?? "—"} · Sequence ${selected.sequence_index}`}>
                <div className="detail-chip-row">
                  <StatusBadge status={selected.generation_state} />
                  <StatusBadge status={selected.approval_state} />
                  {selected.continuity_mode === "frame_chain" && <Badge tone="blue">Frame chain</Badge>}
                  {selected.requires_reference_lock && <Badge tone="warning">Reference lock</Badge>}
                </div>
                {(selected.lint_report ?? []).length > 0 && (
                  <div className="lint-list">
                    <strong>Linter report · {(selected.lint_report ?? []).length}</strong>
                    {selected.lint_report.map((issue, index) => (
                      <div className={`lint-issue lint-${issue.severity}`} key={`${issue.code}-${index}`}>
                        <span>{issue.code}</span><p>{issue.message}</p>
                      </div>
                    ))}
                  </div>
                )}
                {(selected.lint_report ?? []).length === 0 && <div className="callout callout-success">Không có issue từ linter đã lưu.</div>}
                <div className="prompt-preview">
                  <div className="prompt-preview-head"><strong>Compiled prompt</strong><small>hash {selected.prompt_hash || "—"}</small></div>
                  <p>{selected.prompt_final || "Chưa có prompt được compile."}</p>
                  {selected.negative_prompt && <details><summary>Negative prompt</summary><p>{selected.negative_prompt}</p></details>}
                </div>
                <form className="form-stack shot-edit-form" onSubmit={(event) => { void saveShot(event); }}>
                  <div className="field-grid-2">
                    <label className="field"><span>Shot size</span><input value={edit.shot_size} onChange={(event) => setEdit({ ...edit, shot_size: event.target.value })} /></label>
                    <label className="field"><span>Camera motion</span><input value={edit.camera_motion} onChange={(event) => setEdit({ ...edit, camera_motion: event.target.value })} /></label>
                    <label className="field"><span>Duration (s)</span><input type="number" min={0.2} max={600} step={0.1} value={edit.duration_seconds} onChange={(event) => setEdit({ ...edit, duration_seconds: Number(event.target.value) })} /></label>
                    <label className="field"><span>Continuity mode</span><select value={edit.continuity_mode} onChange={(event) => setEdit({ ...edit, continuity_mode: event.target.value })}><option value="none">none</option><option value="previous_shot">previous_shot</option><option value="frame_chain">frame_chain</option></select></label>
                  </div>
                  <label className="field"><span>Composition</span><textarea rows={2} value={edit.composition} onChange={(event) => setEdit({ ...edit, composition: event.target.value })} /></label>
                  <label className="field"><span>Action</span><textarea rows={4} value={edit.action} onChange={(event) => setEdit({ ...edit, action: event.target.value })} /></label>
                  <label className="field"><span>Approval status</span><select value={edit.approval_state} onChange={(event) => setEdit({ ...edit, approval_state: event.target.value as ApprovalState })}><option value="draft">draft</option><option value="needs_review">needs_review</option><option value="approved">approved</option><option value="rejected">rejected</option></select></label>
                  <div className="editor-footer"><span className="muted-text">Sau khi lưu, prompt hash và lint được recompile.</span><button className="button button-primary" disabled={saving}>{saving ? "Đang lưu…" : "Lưu thay đổi"}</button></div>
                </form>
              </Panel>

              <Panel title="Takes" description="Chọn take đã render thành công để đưa lên timeline.">
                {takesLoading ? <LoadingLine label="Đang tải takes…" /> : takes.length === 0 ? <EmptyState title="Chưa có take" detail="Khởi chạy task cho shot trong Queue Monitor để tạo video." /> : (
                  <div className="takes-list">
                    {takes.map((take) => (
                      <div className={`take-row ${take.is_selected ? "take-selected" : ""}`} key={take.id}>
                        <div><strong>Take #{take.take_number}</strong><small>{take.model_id} · {take.id}</small><small>Asset {take.asset_id}</small></div>
                        <div className="take-actions">{take.is_selected && <Badge tone="good">Selected</Badge>}{take.continuity_flagged && <Badge tone="warning">Continuity flag</Badge>}{!take.is_selected && <button className="button button-secondary button-small" onClick={() => { void chooseTake(take); }}>Select take</button>}</div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
