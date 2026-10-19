import { useEffect, useState } from "react";
import type { BibleEntry } from "../services/api";
import type { ModuleProps } from "./shared";
import { Badge, EmptyState, ErrorState, LoadingLine, PageHeader, Panel } from "./shared";

const EMPTY_ATTRIBUTES = "{}";

export default function StoryBible({ api, project, refreshToken, notify, onChanged }: ModuleProps) {
  const [entries, setEntries] = useState<BibleEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [form, setForm] = useState({
    entry_type: "character" as BibleEntry["entry_type"],
    code_name: "",
    display_name: "",
    immutable_traits: "",
    mutable_attributes: EMPTY_ATTRIBUTES,
    reference_asset_ids: "",
    requires_reference_lock: false,
  });

  async function load() {
    setLoading(true);
    setError("");
    try {
      setEntries(await api.listBible(project.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được Story Bible.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void load(); }, [project.id, refreshToken]);

  async function saveEntry(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    let attributes: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(form.mutable_attributes || "{}");
      if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
        throw new Error("Mutable attributes phải là JSON object.");
      }
      attributes = parsed as Record<string, unknown>;
    } catch (err) {
      notify(err instanceof Error ? err.message : "Mutable attributes không phải JSON hợp lệ.", "error");
      return;
    }
    setBusy(true);
    try {
      await api.upsertBibleEntry(project.id, {
        entry_type: form.entry_type,
        code_name: form.code_name.trim(),
        display_name: form.display_name.trim(),
        immutable_traits: form.immutable_traits,
        mutable_attributes: attributes,
        reference_asset_ids: form.reference_asset_ids.split(",").map((item) => item.trim()).filter(Boolean),
        requires_reference_lock: form.requires_reference_lock,
      });
      setForm({ entry_type: form.entry_type, code_name: "", display_name: "", immutable_traits: "", mutable_attributes: EMPTY_ATTRIBUTES, reference_asset_ids: "", requires_reference_lock: false });
      notify("Đã lưu Story Bible và recompile shot prompts.", "success");
      await load();
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không lưu được Story Bible entry.", "error");
    } finally {
      setBusy(false);
    }
  }

  const filtered = typeFilter === "all" ? entries : entries.filter((entry) => entry.entry_type === typeFilter);
  const typeCounts = entries.reduce<Record<string, number>>((counts, entry) => {
    counts[entry.entry_type] = (counts[entry.entry_type] ?? 0) + 1;
    return counts;
  }, {});

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M2 · STORY BIBLE"
        title="Story Bible"
        description="Giữ character, location, style và prop nhất quán giữa các shot. Thay đổi sẽ kích hoạt recompile prompt/lint của project."
      />
      <div className="content-grid content-grid-editor">
        <Panel title="Danh mục tham chiếu" description={`${entries.length} entries · ${typeCounts.character ?? 0} characters · ${typeCounts.location ?? 0} locations · ${typeCounts.style ?? 0} styles`}>
          <div className="toolbar-row">
            <label className="field field-inline">
              <span>Loại entry</span>
              <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
                <option value="all">Tất cả loại</option>
                <option value="character">Character</option>
                <option value="location">Location</option>
                <option value="style">Style</option>
                <option value="prop">Prop</option>
              </select>
            </label>
            <button className="button button-quiet button-small" onClick={() => { void load(); }}>↻ Refresh</button>
          </div>
          {loading ? <LoadingLine /> : error ? <ErrorState message={error} onRetry={() => { void load(); }} /> : filtered.length === 0 ? (
            <EmptyState title="Chưa có entry phù hợp" detail="Thêm entry bên cạnh hoặc import kịch bản trong Script Studio để tạo Story Bible ban đầu." />
          ) : (
            <div className="bible-list">
              {filtered.map((entry) => (
                <article className="bible-card" key={entry.id}>
                  <div className="bible-card-top">
                    <Badge tone={entry.entry_type === "character" ? "blue" : entry.entry_type === "style" ? "good" : "neutral"}>{entry.entry_type}</Badge>
                    <code>{entry.code_name}</code>
                  </div>
                  <h3>{entry.display_name}</h3>
                  <p>{entry.immutable_traits || "Chưa định nghĩa traits bất biến."}</p>
                  <div className="bible-meta">
                    <span>{entry.reference_asset_ids.length} reference asset(s)</span>
                    {entry.requires_reference_lock && <Badge tone="warning">Reference lock required</Badge>}
                  </div>
                  {Object.keys(entry.mutable_attributes ?? {}).length > 0 && (
                    <details className="json-details">
                      <summary>Mutable attributes</summary>
                      <pre>{JSON.stringify(entry.mutable_attributes, null, 2)}</pre>
                    </details>
                  )}
                </article>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="Thêm hoặc cập nhật entry" description="Dùng code_name ổn định; lưu entry hiện có cùng code_name sẽ cập nhật entry đó.">
          <form className="form-stack" onSubmit={(event) => { void saveEntry(event); }}>
            <label className="field">
              <span>Entry type</span>
              <select value={form.entry_type} onChange={(event) => setForm({ ...form, entry_type: event.target.value as BibleEntry["entry_type"] })}>
                <option value="character">Character</option>
                <option value="location">Location</option>
                <option value="style">Style</option>
                <option value="prop">Prop</option>
              </select>
            </label>
            <label className="field">
              <span>Code name</span>
              <input required minLength={2} maxLength={128} value={form.code_name} onChange={(event) => setForm({ ...form, code_name: event.target.value })} placeholder="vd. conan_edogawa" />
            </label>
            <label className="field">
              <span>Display name</span>
              <input required maxLength={255} value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Tên hiển thị" />
            </label>
            <label className="field">
              <span>Immutable traits</span>
              <textarea rows={4} value={form.immutable_traits} onChange={(event) => setForm({ ...form, immutable_traits: event.target.value })} placeholder="Đặc điểm không được thay đổi giữa các shot…" />
            </label>
            <label className="field">
              <span>Mutable attributes · JSON</span>
              <textarea className="code-editor" rows={4} value={form.mutable_attributes} onChange={(event) => setForm({ ...form, mutable_attributes: event.target.value })} spellCheck={false} />
            </label>
            <label className="field">
              <span>Reference asset IDs · phân tách bằng dấu phẩy</span>
              <input value={form.reference_asset_ids} onChange={(event) => setForm({ ...form, reference_asset_ids: event.target.value })} placeholder="ast_123, ast_456" />
            </label>
            <label className="checkbox-row">
              <input type="checkbox" checked={form.requires_reference_lock} onChange={(event) => setForm({ ...form, requires_reference_lock: event.target.checked })} />
              <span>Yêu cầu reference lock</span>
            </label>
            <button className="button button-primary full-width" type="submit" disabled={busy}>
              {busy ? "Đang lưu…" : "Lưu entry & recompile"}
            </button>
          </form>
        </Panel>
      </div>
    </div>
  );
}
