import React, { useEffect, useMemo, useState } from 'react';
import './styles.css';

type ViewKey = 'projects' | 'script' | 'bible' | 'shots';
type ApiState = 'checking' | 'online' | 'offline' | 'unauthorized';
type NoticeKind = 'success' | 'error' | 'info';
type BibleType = 'character' | 'location' | 'style' | 'prop';

interface Project {
  id: string; name: string; description: string; aspect_ratio: string; fps: number;
  style_preset: string; language: string; output_profile: string; current_revision_num: number;
  asset_count: number; created_at: string; updated_at: string;
}
interface BibleEntry {
  id: string; entry_type: BibleType; code_name: string; display_name: string;
  immutable_traits: string; mutable_attributes: Record<string, unknown>;
  reference_asset_ids: string[]; requires_reference_lock: boolean;
}
interface LintIssue { code: string; severity: string; message: string; field?: string | null; }
interface Shot {
  id: string; scene_id: string; sequence_index: number; title: string; subject_codes: string[];
  location_code: string; shot_size: string; camera_motion: string; composition: string; action: string;
  dialogue: string; duration_seconds: number; requires_reference_lock: boolean; reference_asset_ids: string[];
  prompt_final: string; negative_prompt: string; prompt_hash: string; lint_report: LintIssue[];
  approval_state: string; generation_state: string;
}
interface ImportResult {
  script_revision_id: string; version_number: number; raw_content_preserved: boolean; diff_summary: string;
  detected_characters: number; detected_locations: number; detected_scenes: number; generated_shots: number;
}
interface Notice { kind: NoticeKind; message: string; }
interface BibleForm {
  entry_type: BibleType; code_name: string; display_name: string; immutable_traits: string;
  mutable_attributes: string; reference_asset_ids: string; requires_reference_lock: boolean;
}
interface ShotForm {
  shot_size: string; camera_motion: string; composition: string; action: string;
  duration_seconds: string; approval_state: string;
}

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.name = 'ApiError'; this.status = status; }
}

const initialApiUrl = (import.meta as any).env?.VITE_ORCHESTRATOR_URL || 'http://localhost:8000';

async function apiRequest<T>(baseUrl: string, token: string, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token.trim()) headers.set('X-Studio-Admin-Token', token.trim());
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');

  let response: Response;
  try {
    response = await fetch(baseUrl.replace(/\/+$/, '') + path, { ...init, headers });
  } catch {
    throw new Error('Không thể kết nối API. Kiểm tra Orchestrator và địa chỉ API.');
  }
  const payload: unknown = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail) : 'Yêu cầu API thất bại.';
    throw new ApiError(detail, response.status);
  }
  return payload as T;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return 'API yêu cầu Admin Token hoặc token không hợp lệ. Mở API & Access để nhập token.';
  return error instanceof Error ? error.message : 'Đã xảy ra lỗi không xác định.';
}
function formatDate(value: string): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('vi-VN', { day: '2-digit', month: 'short', year: 'numeric' }).format(date);
}
function labelForBibleType(type: string): string {
  const labels: Record<string, string> = { character: 'Nhân vật', location: 'Bối cảnh', style: 'Phong cách', prop: 'Đạo cụ' };
  return labels[type] || type;
}
function labelForState(state: string): string {
  const labels: Record<string, string> = {
    draft: 'Bản nháp', needs_review: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Từ chối',
    not_started: 'Chưa tạo', blocked_reference: 'Thiếu reference', blocked_upstream_frame: 'Thiếu frame đầu vào',
    queued: 'Trong hàng đợi', running: 'Đang chạy', succeeded: 'Thành công', failed: 'Thất bại', canceled: 'Đã hủy',
  };
  return labels[state] || state.replace(/_/g, ' ');
}
function toneForState(state: string): string {
  if (['approved', 'succeeded', 'online', 'healthy'].includes(state)) return 'success';
  if (['blocked_reference', 'blocked_upstream_frame', 'failed', 'rejected', 'unauthorized'].includes(state)) return 'danger';
  if (['needs_review', 'queued', 'running', 'warning', 'degraded'].includes(state)) return 'warning';
  return 'neutral';
}
function slugForEntry(type: BibleType, value: string): string {
  const prefix: Record<BibleType, string> = { character: 'char', location: 'loc', style: 'style', prop: 'prop' };
  const normalized = value.toLowerCase().replace(/đ/g, 'd').normalize('NFKD').replace(/[\u0300-\u036f]/g, '');
  const clean = normalized.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 24);
  return prefix[type] + '_' + (clean || 'new_entry');
}
function emptyBibleForm(type: BibleType = 'character'): BibleForm {
  return { entry_type: type, code_name: slugForEntry(type, ''), display_name: '', immutable_traits: '', mutable_attributes: '{}', reference_asset_ids: '', requires_reference_lock: false };
}
function bibleFormFromEntry(entry: BibleEntry): BibleForm {
  return {
    entry_type: entry.entry_type, code_name: entry.code_name, display_name: entry.display_name,
    immutable_traits: entry.immutable_traits || '', mutable_attributes: JSON.stringify(entry.mutable_attributes || {}, null, 2),
    reference_asset_ids: (entry.reference_asset_ids || []).join(', '), requires_reference_lock: Boolean(entry.requires_reference_lock),
  };
}
function shotFormFromShot(shot: Shot): ShotForm {
  return { shot_size: shot.shot_size || 'medium_shot', camera_motion: shot.camera_motion || 'static',
    composition: shot.composition || '', action: shot.action || '', duration_seconds: String(shot.duration_seconds ?? 5),
    approval_state: shot.approval_state || 'needs_review' };
}

export const App: React.FC = () => {
  const [view, setView] = useState<ViewKey>('projects');
  const [apiBaseUrl, setApiBaseUrl] = useState<string>(initialApiUrl);
  const [apiBaseDraft, setApiBaseDraft] = useState<string>(initialApiUrl);
  const [adminToken, setAdminToken] = useState<string>('');
  const [adminTokenDraft, setAdminTokenDraft] = useState<string>('');
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [apiState, setApiState] = useState<ApiState>('checking');
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [workspaceRefresh, setWorkspaceRefresh] = useState<number>(0);
  const [projectsLoading, setProjectsLoading] = useState<boolean>(true);
  const [workspaceLoading, setWorkspaceLoading] = useState<boolean>(false);
  const [bibleEntries, setBibleEntries] = useState<BibleEntry[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [apiError, setApiError] = useState<string>('');

  const [showCreateProject, setShowCreateProject] = useState<boolean>(false);
  const [projectForm, setProjectForm] = useState({
    name: '', description: '', aspect_ratio: '16:9', fps: '24',
    style_preset: '2d_anime_cinematic', language: 'vi', output_profile: '1080p_h264',
  });
  const [creatingProject, setCreatingProject] = useState<boolean>(false);
  const [scriptText, setScriptText] = useState<string>('');
  const [scriptFormat, setScriptFormat] = useState<string>('museflow');
  const [importingScript, setImportingScript] = useState<boolean>(false);
  const [lastImport, setLastImport] = useState<ImportResult | null>(null);
  const [bibleFilter, setBibleFilter] = useState<string>('all');
  const [bibleSearch, setBibleSearch] = useState<string>('');
  const [editingBibleEntry, setEditingBibleEntry] = useState<BibleEntry | null>(null);
  const [bibleForm, setBibleForm] = useState<BibleForm>(emptyBibleForm());
  const [savingBible, setSavingBible] = useState<boolean>(false);
  const [selectedShotId, setSelectedShotId] = useState<string>('');
  const [shotForm, setShotForm] = useState<ShotForm | null>(null);
  const [savingShot, setSavingShot] = useState<boolean>(false);

  const selectedProject = useMemo(() => projects.find((project) => project.id === selectedProjectId) || null, [projects, selectedProjectId]);
  const selectedShot = useMemo(() => shots.find((shot) => shot.id === selectedShotId) || null, [shots, selectedShotId]);
  const filteredBibleEntries = useMemo(() => {
    const query = bibleSearch.trim().toLocaleLowerCase();
    return bibleEntries.filter((entry) => {
      const matchesType = bibleFilter === 'all' || entry.entry_type === bibleFilter;
      const matchesSearch = !query || (entry.display_name + ' ' + entry.code_name + ' ' + entry.immutable_traits).toLocaleLowerCase().includes(query);
      return matchesType && matchesSearch;
    });
  }, [bibleEntries, bibleFilter, bibleSearch]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setProjectsLoading(true); setApiState('checking'); setApiError('');
      try {
        const health = await apiRequest<{ status: string }>(apiBaseUrl, adminToken, '/healthz');
        if (health.status !== 'ok') throw new Error('Orchestrator trả về trạng thái health không xác định.');
        if (active) setApiState('online');
        const nextProjects = await apiRequest<Project[]>(apiBaseUrl, adminToken, '/api/v1/projects');
        if (!active) return;
        setProjects(nextProjects);
        setSelectedProjectId((current) => nextProjects.some((project) => project.id === current) ? current : (nextProjects[0]?.id || ''));
      } catch (error) {
        if (!active) return;
        setApiState(error instanceof ApiError && error.status === 401 ? 'unauthorized' : 'offline');
        setApiError(errorMessage(error));
      } finally {
        if (active) setProjectsLoading(false);
      }
    };
    void load();
    return () => { active = false; };
  }, [apiBaseUrl, adminToken, workspaceRefresh]);

  useEffect(() => {
    let active = true;
    if (!selectedProjectId) {
      setBibleEntries([]); setShots([]); setSelectedShotId(''); setShotForm(null); setWorkspaceLoading(false);
      return () => { active = false; };
    }
    const loadWorkspace = async () => {
      setWorkspaceLoading(true);
      try {
        const prefix = '/api/v1/projects/' + encodeURIComponent(selectedProjectId);
        const [nextBible, nextShots] = await Promise.all([
          apiRequest<BibleEntry[]>(apiBaseUrl, adminToken, prefix + '/story-bible'),
          apiRequest<Shot[]>(apiBaseUrl, adminToken, prefix + '/shots'),
        ]);
        if (!active) return;
        setBibleEntries(nextBible); setShots(nextShots);
        setSelectedShotId((current) => nextShots.some((shot) => shot.id === current) ? current : (nextShots[0]?.id || ''));
      } catch (error) {
        if (active) setNotice({ kind: 'error', message: errorMessage(error) });
      } finally {
        if (active) setWorkspaceLoading(false);
      }
    };
    void loadWorkspace();
    return () => { active = false; };
  }, [apiBaseUrl, adminToken, selectedProjectId, workspaceRefresh]);

  useEffect(() => {
    setShotForm(selectedShot ? shotFormFromShot(selectedShot) : null);
  }, [selectedShot]);

  function notify(kind: NoticeKind, message: string) { setNotice({ kind, message }); }

  async function handleCreateProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectForm.name.trim()) return;
    setCreatingProject(true);
    try {
      const created = await apiRequest<Project>(apiBaseUrl, adminToken, '/api/v1/projects', {
        method: 'POST',
        body: JSON.stringify({
          name: projectForm.name.trim(), description: projectForm.description.trim(),
          aspect_ratio: projectForm.aspect_ratio, fps: Number(projectForm.fps),
          style_preset: projectForm.style_preset.trim() || '2d_anime_cinematic',
          language: projectForm.language, output_profile: projectForm.output_profile,
        }),
      });
      setSelectedProjectId(created.id);
      setProjectForm({ name: '', description: '', aspect_ratio: '16:9', fps: '24', style_preset: '2d_anime_cinematic', language: 'vi', output_profile: '1080p_h264' });
      setShowCreateProject(false); setView('script'); setWorkspaceRefresh((count) => count + 1);
      notify('success', 'Đã tạo project mới. Bắt đầu bằng việc import kịch bản.');
    } catch (error) { notify('error', errorMessage(error)); }
    finally { setCreatingProject(false); }
  }

  async function handleImportScript(applyProposal: boolean) {
    if (!selectedProject || scriptText.trim().length < 5) {
      notify('error', 'Chọn project và nhập kịch bản có ít nhất 5 ký tự.'); return;
    }
    if (applyProposal && (shots.length > 0 || bibleEntries.length > 0)) {
      const confirmed = window.confirm('Áp dụng proposal sẽ thay thế toàn bộ Story Bible, scenes và shots hiện có trong project. Kịch bản gốc vẫn được lưu thành revision. Tiếp tục?');
      if (!confirmed) return;
    }
    setImportingScript(true); setLastImport(null);
    try {
      const result = await apiRequest<ImportResult>(apiBaseUrl, adminToken,
        '/api/v1/projects/' + encodeURIComponent(selectedProject.id) + '/scripts/import', {
          method: 'POST', body: JSON.stringify({ raw_content: scriptText, source_format: scriptFormat, auto_apply_proposal: applyProposal }),
        });
      setLastImport(result); setWorkspaceRefresh((count) => count + 1);
      if (applyProposal) {
        setView('shots');
        notify('success', 'Đã áp dụng proposal và tạo ' + result.generated_shots + ' shots. Kiểm tra lint trước khi duyệt.');
      } else {
        notify('success', 'Đã lưu revision #' + result.version_number + '. Story Bible và Shot Board chưa bị thay đổi.');
      }
    } catch (error) { notify('error', errorMessage(error)); }
    finally { setImportingScript(false); }
  }

  function startNewBibleEntry() { setEditingBibleEntry(null); setBibleForm(emptyBibleForm()); }
  function startEditBibleEntry(entry: BibleEntry) { setEditingBibleEntry(entry); setBibleForm(bibleFormFromEntry(entry)); }

  async function handleSaveBibleEntry(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProject) return;
    let mutableAttributes: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(bibleForm.mutable_attributes || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Expected a JSON object.');
      mutableAttributes = parsed as Record<string, unknown>;
    } catch {
      notify('error', 'Mutable attributes phải là JSON object hợp lệ, ví dụ {"current_outfit":"signature outfit"}.'); return;
    }
    setSavingBible(true);
    try {
      await apiRequest<unknown>(apiBaseUrl, adminToken,
        '/api/v1/projects/' + encodeURIComponent(selectedProject.id) + '/story-bible', {
          method: 'POST',
          body: JSON.stringify({
            entry_type: bibleForm.entry_type, code_name: bibleForm.code_name.trim(),
            display_name: bibleForm.display_name.trim(), immutable_traits: bibleForm.immutable_traits.trim(),
            mutable_attributes: mutableAttributes,
            reference_asset_ids: bibleForm.reference_asset_ids.split(',').map((id) => id.trim()).filter(Boolean),
            requires_reference_lock: bibleForm.requires_reference_lock,
          }),
        });
      setEditingBibleEntry(null); setBibleForm(emptyBibleForm()); setWorkspaceRefresh((count) => count + 1);
      notify('success', 'Đã lưu Story Bible và biên dịch lại prompt cho các shots.');
    } catch (error) { notify('error', errorMessage(error)); }
    finally { setSavingBible(false); }
  }

  async function handleSaveShot(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProject || !selectedShot || !shotForm) return;
    const duration = Number(shotForm.duration_seconds);
    if (!Number.isFinite(duration) || duration <= 0) { notify('error', 'Thời lượng shot phải lớn hơn 0 giây.'); return; }
    setSavingShot(true);
    try {
      await apiRequest<unknown>(apiBaseUrl, adminToken,
        '/api/v1/projects/' + encodeURIComponent(selectedProject.id) + '/shots/' + encodeURIComponent(selectedShot.id), {
          method: 'PATCH',
          body: JSON.stringify({
            shot_size: shotForm.shot_size, camera_motion: shotForm.camera_motion,
            composition: shotForm.composition, action: shotForm.action,
            duration_seconds: duration, approval_state: shotForm.approval_state,
          }),
        });
      setWorkspaceRefresh((count) => count + 1);
      notify('success', 'Đã lưu shot. Prompt và continuity lint đã được tính lại.');
    } catch (error) {
      notify('error', errorMessage(error)); setWorkspaceRefresh((count) => count + 1);
    } finally { setSavingShot(false); }
  }

  const navItems: { key: ViewKey; icon: string; title: string; subtitle: string }[] = [
    { key: 'projects', icon: '▦', title: 'Projects', subtitle: 'Dự án & phiên bản' },
    { key: 'script', icon: '≡', title: 'Script Studio', subtitle: 'Import & phân tích' },
    { key: 'bible', icon: '◈', title: 'Story Bible', subtitle: 'Character · Location · Style' },
    { key: 'shots', icon: '▤', title: 'Shot Board', subtitle: 'Prompt · Lint · Approval' },
  ];
  const activeTitle = navItems.find((item) => item.key === view)?.title || 'Workspace';

  return (
    <div className="studio-shell">
      <aside className="sidebar">
        <div className="brand-lockup"><div className="brand-mark">M</div><div><div className="brand-name">MuseStudio</div><div className="brand-caption">AI FILM WORKSPACE</div></div></div>
        <div className="sidebar-section-label">WORKSPACE</div>
        <nav className="main-nav" aria-label="Workspace navigation">
          {navItems.map((item) => (
            <button className={'nav-item' + (view === item.key ? ' active' : '')} key={item.key} onClick={() => setView(item.key)} type="button">
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              <span className="nav-copy"><span className="nav-title">{item.title}</span><span className="nav-subtitle">{item.subtitle}</span></span>
              {view === item.key && <span className="nav-current" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-projects">
          <div className="sidebar-section-heading"><span>RECENT PROJECTS</span><button type="button" className="icon-button small" aria-label="Tạo project" onClick={() => { setView('projects'); setShowCreateProject(true); }}>+</button></div>
          {projects.length === 0 ? <p className="sidebar-empty">{projectsLoading ? 'Đang tải project…' : 'Chưa có project nào.'}</p> : projects.slice(0, 5).map((project) => (
            <button type="button" key={project.id} className={'recent-project' + (selectedProjectId === project.id ? ' selected' : '')} onClick={() => { setSelectedProjectId(project.id); setView('script'); }} title={project.name}>
              <span className="project-dot" /><span className="recent-project-name">{project.name}</span>
            </button>
          ))}
        </div>
        <div className="sidebar-spacer" />
        <div className="sidebar-roadmap">
          <div className="roadmap-heading">BUILD STATUS <span className="roadmap-pulse" /></div>
          <div className="roadmap-line"><span className="roadmap-check">✓</span><span>M0 · Audit & Contracts</span></div>
          <div className="roadmap-line"><span className="roadmap-check">✓</span><span>M1 · Foundation</span></div>
          <div className="roadmap-line current"><span className="roadmap-current-dot" /><span>M2 · Script Workspace</span></div>
          <div className="roadmap-line future"><span className="roadmap-future-dot" /><span>M3 · Production DAG UI</span></div>
          <div className="roadmap-line future"><span className="roadmap-future-dot" /><span>M4 · Async Render Queue</span></div>
        </div>
        <button type="button" className="access-button" onClick={() => { setApiBaseDraft(apiBaseUrl); setAdminTokenDraft(adminToken); setSettingsOpen(true); }}>
          <span className="access-icon">⚙</span><span><strong>API & Access</strong><small>Endpoint · Admin token</small></span><span className="access-chevron">›</span>
        </button>
      </aside>

      <main className="studio-main">
        <header className="topbar">
          <div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-divider">/</span><strong>{activeTitle}</strong></div>
          <div className="topbar-actions">
            <div className="api-indicator"><span className={'api-dot ' + apiState} /><span>{apiState === 'checking' ? 'Checking API' : apiState === 'online' ? 'API Connected' : apiState === 'unauthorized' ? 'Auth Required' : 'API Offline'}</span></div>
            <button type="button" className="icon-button refresh-button" title="Tải lại dữ liệu" onClick={() => setWorkspaceRefresh((count) => count + 1)}>↻</button>
            <button type="button" className="button button-primary button-compact" onClick={() => { setView('projects'); setShowCreateProject(true); }}><span>＋</span> New project</button>
          </div>
        </header>

        {notice && <div className={'notice notice-' + notice.kind} role="status"><span className="notice-symbol">{notice.kind === 'success' ? '✓' : notice.kind === 'error' ? '!' : 'i'}</span><span>{notice.message}</span><button type="button" className="notice-close" aria-label="Đóng thông báo" onClick={() => setNotice(null)}>×</button></div>}
        {(apiState === 'offline' || apiState === 'unauthorized') && <div className="connection-banner"><div><strong>{apiState === 'unauthorized' ? 'Cần xác thực để truy cập API' : 'Chưa kết nối được Orchestrator'}</strong><p>{apiError || 'Kiểm tra API URL, server và cấu hình kết nối.'}</p></div><button type="button" className="button button-secondary" onClick={() => { setApiBaseDraft(apiBaseUrl); setAdminTokenDraft(adminToken); setSettingsOpen(true); }}>Mở cấu hình</button></div>}

        <section className="page-heading">
          <div><div className="eyebrow">MUSESTUDIO / PRODUCTION</div><h1>{activeTitle}</h1>
            <p className="page-description">
              {view === 'projects' && 'Quản lý project, output profile và revision của từng phim.'}
              {view === 'script' && 'Giữ nguyên kịch bản gốc, phân tích cấu trúc và quyết định khi nào áp dụng proposal.'}
              {view === 'bible' && 'Quản lý các quy tắc bất biến để giữ nhân vật, bối cảnh và phong cách nhất quán.'}
              {view === 'shots' && 'Kiểm tra prompt, continuity lint và trạng thái duyệt trước khâu render.'}
            </p>
          </div>
          <div className="project-context"><span className="context-label">ACTIVE PROJECT</span>
            <select aria-label="Project đang làm việc" value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)} disabled={projects.length === 0}>
              {projects.length === 0 ? <option value="">Chưa có project</option> : projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
            <span className="context-meta">{selectedProject ? selectedProject.aspect_ratio + ' · ' + selectedProject.fps + ' fps' : 'Chưa chọn project'}</span>
          </div>
        </section>

        {view === 'projects' && <div className="page-content">
          <div className="stats-grid">
            <StatCard label="TOTAL PROJECTS" value={String(projects.length).padStart(2, '0')} detail="Các project trong workspace" icon="▦" />
            <StatCard label="STORY BIBLE" value={String(bibleEntries.length).padStart(2, '0')} detail="Entries của project đang chọn" icon="◈" />
            <StatCard label="SHOT BOARD" value={String(shots.length).padStart(2, '0')} detail="Shots đã phân tích / nhập" icon="▤" />
            <StatCard label="APPROVED SHOTS" value={String(shots.filter((shot) => shot.approval_state === 'approved').length).padStart(2, '0')} detail="Đã sẵn sàng qua bước review" icon="✓" />
          </div>
          {showCreateProject && <section className="panel create-project-panel">
            <div className="panel-heading"><div><span className="panel-kicker">GET STARTED</span><h2>Tạo project mới</h2></div><button type="button" className="icon-button" onClick={() => setShowCreateProject(false)} aria-label="Đóng">×</button></div>
            <form className="form-grid" onSubmit={handleCreateProject}>
              <label className="field field-wide">Tên project<input autoFocus required maxLength={255} value={projectForm.name} onChange={(event) => setProjectForm({ ...projectForm, name: event.target.value })} placeholder="Ví dụ: Conan — Case 02" /></label>
              <label className="field field-wide">Mô tả<textarea rows={2} maxLength={2000} value={projectForm.description} onChange={(event) => setProjectForm({ ...projectForm, description: event.target.value })} placeholder="Mục tiêu hoặc logline của phim" /></label>
              <label className="field">Aspect ratio<select value={projectForm.aspect_ratio} onChange={(event) => setProjectForm({ ...projectForm, aspect_ratio: event.target.value })}><option>16:9</option><option>9:16</option><option>1:1</option><option>21:9</option></select></label>
              <label className="field">Frame rate<select value={projectForm.fps} onChange={(event) => setProjectForm({ ...projectForm, fps: event.target.value })}><option value="24">24 fps · Cinema</option><option value="25">25 fps</option><option value="30">30 fps</option><option value="60">60 fps</option></select></label>
              <label className="field">Style preset<input value={projectForm.style_preset} onChange={(event) => setProjectForm({ ...projectForm, style_preset: event.target.value })} /></label>
              <label className="field">Ngôn ngữ<select value={projectForm.language} onChange={(event) => setProjectForm({ ...projectForm, language: event.target.value })}><option value="vi">Tiếng Việt</option><option value="en">English</option><option value="ja">日本語</option></select></label>
              <div className="form-actions field-wide"><button className="button button-primary" type="submit" disabled={creatingProject}>{creatingProject ? 'Đang tạo…' : 'Create project'}</button><button className="button button-ghost" type="button" onClick={() => setShowCreateProject(false)}>Hủy</button></div>
            </form>
          </section>}
          <div className="section-heading"><div><h2>All projects</h2><p>Chọn project để mở Script Studio và các công cụ biên tập.</p></div><button type="button" className="button button-secondary" onClick={() => setShowCreateProject((open) => !open)}>＋ Tạo project</button></div>
          {projectsLoading ? <LoadingState label="Đang tải danh sách project…" /> : projects.length === 0 ? <EmptyState icon="▦" title="Workspace đang trống" description="Tạo project đầu tiên để bắt đầu import kịch bản và xây dựng Shot Board." actionLabel="Tạo project đầu tiên" onAction={() => setShowCreateProject(true)} /> :
            <div className="project-grid">{projects.map((project) => <button type="button" className={'project-card' + (selectedProjectId === project.id ? ' project-card-selected' : '')} key={project.id} onClick={() => { setSelectedProjectId(project.id); setView('script'); }}>
              <div className="project-card-top"><span className="project-art-icon">M</span><span className="project-open">↗</span></div><h3>{project.name}</h3><p>{project.description || 'Chưa có mô tả cho project này.'}</p>
              <div className="project-tags"><span>{project.aspect_ratio}</span><span>{project.fps} fps</span><span>Rev {project.current_revision_num}</span></div>
              <div className="project-card-footer"><span>{project.style_preset}</span><span>{formatDate(project.updated_at)}</span></div>
            </button>)}</div>}
        </div>}

        {view === 'script' && <div className="page-content">
          {!selectedProject ? <EmptyState icon="≡" title="Chưa có project" description="Tạo hoặc chọn một project trước khi import kịch bản." actionLabel="Tạo project" onAction={() => { setView('projects'); setShowCreateProject(true); }} /> : <div className="editor-layout">
            <section className="panel script-panel">
              <div className="panel-heading"><div><span className="panel-kicker">M2 · SCRIPT INTAKE</span><h2>Import source script</h2></div><span className="file-type-pill">RAW SOURCE PRESERVED</span></div>
              <p className="panel-description">Dán kịch bản Markdown, plain text hoặc MuseFlow Node Blocks. Bản gốc được lưu thành revision; bản phân tích là một proposal riêng.</p>
              <div className="script-toolbar"><label className="field compact-field">Source format<select value={scriptFormat} onChange={(event) => setScriptFormat(event.target.value)}><option value="museflow">MuseFlow Node Blocks</option><option value="markdown">Markdown</option><option value="txt">Plain text</option><option value="json">JSON</option></select></label><span className="character-count">{scriptText.length.toLocaleString()} characters</span></div>
              <textarea className="script-textarea" value={scriptText} onChange={(event) => setScriptText(event.target.value)} placeholder={'SCENE 1: Quán Cà Phê Poirot — Đêm mưa\nSHOT 1: Conan quan sát chiếc cốc\n- Character: Conan Edogawa\n- Location: Quán Cà Phê Poirot\n- Camera: slow push-in\n- Action: Conan nhìn xuống chiếc cốc, rồi ngước mắt về phía cửa sổ\n- Duration: 4.5\n\nSHOT 2: Ran nhận ra dấu hiệu bất thường\n- Character: Ran Mouri\n- Camera: static\n- Action: Ran đặt cốc xuống và nghiêng người nhìn Conan\n- Duration: 5.0'} spellCheck={false} />
              <div className="script-actions">
                <button type="button" className="button button-secondary" disabled={importingScript || scriptText.trim().length < 5 || apiState !== 'online'} onClick={() => void handleImportScript(false)}>{importingScript ? 'Đang xử lý…' : 'Analyze & save revision'}</button>
                <button type="button" className="button button-primary" disabled={importingScript || scriptText.trim().length < 5 || apiState !== 'online'} onClick={() => void handleImportScript(true)}>{importingScript ? 'Đang xử lý…' : 'Apply to Story Bible + Shots'} <span>→</span></button>
              </div>
              <div className="callout callout-warning"><span className="callout-icon">!</span><p><strong>Apply proposal sẽ thay thế workspace hiện tại.</strong> Nếu đã có Story Bible hoặc shots, ứng dụng sẽ yêu cầu xác nhận trước khi ghi đè. Dùng Analyze & save revision nếu chỉ muốn lưu revision và phân tích.</p></div>
              {lastImport && <div className="import-result">
                <div className="import-result-heading"><span className="result-check">✓</span><div><strong>Revision #{lastImport.version_number} saved</strong><small>{lastImport.raw_content_preserved ? 'Original script preserved' : 'Check source retention'}</small></div></div>
                <div className="result-metrics"><div><strong>{lastImport.detected_scenes}</strong><span>Scenes detected</span></div><div><strong>{lastImport.detected_characters}</strong><span>Characters</span></div><div><strong>{lastImport.detected_locations}</strong><span>Locations</span></div><div><strong>{lastImport.generated_shots}</strong><span>Shots applied</span></div></div>
                {lastImport.diff_summary && <details className="diff-details"><summary>View structured proposal diff</summary><pre>{lastImport.diff_summary}</pre></details>}
              </div>}
            </section>
            <aside className="side-stack">
              <section className="panel checklist-panel"><div className="panel-heading"><div><span className="panel-kicker">PIPELINE STATUS</span><h2>Before rendering</h2></div></div>
                <PipelineStep number="01" title="Script revision" description="Lưu nguyên văn nguồn và tạo proposal có thể kiểm tra." state="done" />
                <PipelineStep number="02" title="Story Bible" description="Đồng bộ nhân vật, bối cảnh và style lock." state={bibleEntries.length ? 'done' : 'pending'} />
                <PipelineStep number="03" title="Shot Board" description="Kiểm tra prompt, blockers và continuity warning." state={shots.length ? 'review' : 'pending'} />
                <PipelineStep number="04" title="Generation" description="Chưa chạy render thật ở milestone hiện tại." state="pending" />
              </section>
              <section className="panel compact-summary"><span className="panel-kicker">CURRENT PROJECT</span><h3>{selectedProject.name}</h3><p>{selectedProject.description || 'Chưa có mô tả.'}</p>
                <div className="summary-row"><span>Output</span><strong>{selectedProject.output_profile}</strong></div><div className="summary-row"><span>Style</span><strong>{selectedProject.style_preset}</strong></div><div className="summary-row"><span>Revision</span><strong>#{selectedProject.current_revision_num}</strong></div>
              </section>
            </aside>
          </div>}
        </div>}

        {view === 'bible' && <div className="page-content">
          {!selectedProject ? <EmptyState icon="◈" title="Chưa có project" description="Story Bible được quản lý riêng theo từng project." actionLabel="Tạo project" onAction={() => { setView('projects'); setShowCreateProject(true); }} /> : <>
            <div className="stats-grid stats-grid-three">
              <StatCard label="CHARACTERS" value={String(bibleEntries.filter((entry) => entry.entry_type === 'character').length).padStart(2, '0')} detail="Identity & appearance locks" icon="◉" />
              <StatCard label="LOCATIONS" value={String(bibleEntries.filter((entry) => entry.entry_type === 'location').length).padStart(2, '0')} detail="Spatial & lighting anchors" icon="⌂" />
              <StatCard label="STYLE / PROPS" value={String(bibleEntries.filter((entry) => entry.entry_type === 'style' || entry.entry_type === 'prop').length).padStart(2, '0')} detail="Global art direction & objects" icon="✳" />
            </div>
            <div className="bible-layout">
              <section className="panel bible-list-panel">
                <div className="section-heading inline-heading"><div><h2>Story Bible entries</h2><p>{bibleEntries.length} entries · {selectedProject.name}</p></div><button type="button" className="button button-secondary button-compact" onClick={startNewBibleEntry}>＋ Add entry</button></div>
                <div className="filter-row"><label className="search-field"><span>⌕</span><input value={bibleSearch} onChange={(event) => setBibleSearch(event.target.value)} placeholder="Tìm tên, code hoặc trait…" /></label>
                  <select aria-label="Lọc loại Story Bible" value={bibleFilter} onChange={(event) => setBibleFilter(event.target.value)}><option value="all">Tất cả loại</option><option value="character">Nhân vật</option><option value="location">Bối cảnh</option><option value="style">Phong cách</option><option value="prop">Đạo cụ</option></select></div>
                {workspaceLoading ? <LoadingState label="Đang tải Story Bible…" /> : filteredBibleEntries.length === 0 ? <EmptyState icon="◈" title="Chưa có entry phù hợp" description="Thêm entry thủ công hoặc apply proposal từ Script Studio." actionLabel="Thêm entry" onAction={startNewBibleEntry} /> :
                  <div className="bible-entry-list">{filteredBibleEntries.map((entry) => <button type="button" key={entry.id} className={'bible-entry' + (editingBibleEntry?.id === entry.id ? ' bible-entry-active' : '')} onClick={() => startEditBibleEntry(entry)}>
                    <div className="bible-entry-top"><span className={'entry-type entry-type-' + entry.entry_type}>{labelForBibleType(entry.entry_type)}</span>{entry.requires_reference_lock && <span className="mini-lock">⌑ Reference</span>}</div>
                    <strong>{entry.display_name}</strong><code>{entry.code_name}</code><p>{entry.immutable_traits || 'Chưa có immutable traits.'}</p>
                  </button>)}</div>}
              </section>
              <section className="panel bible-editor-panel">
                <div className="panel-heading"><div><span className="panel-kicker">{editingBibleEntry ? 'EDIT ENTRY' : 'NEW ENTRY'}</span><h2>{editingBibleEntry ? 'Chỉnh sửa Bible' : 'Thêm vào Story Bible'}</h2></div>{editingBibleEntry && <button type="button" className="button button-ghost button-compact" onClick={startNewBibleEntry}>Hủy sửa</button>}</div>
                <form className="stack-form" onSubmit={(event) => void handleSaveBibleEntry(event)}>
                  <label className="field">Entry type<select value={bibleForm.entry_type} disabled={Boolean(editingBibleEntry)} onChange={(event) => { const type = event.target.value as BibleType; setBibleForm({ ...bibleForm, entry_type: type, code_name: slugForEntry(type, bibleForm.display_name) }); }}><option value="character">Character</option><option value="location">Location</option><option value="style">Style</option><option value="prop">Prop</option></select>{editingBibleEntry && <small>Loại entry hiện có được giữ nguyên bởi API.</small>}</label>
                  <label className="field">Code name<input required maxLength={128} value={bibleForm.code_name} onChange={(event) => setBibleForm({ ...bibleForm, code_name: event.target.value })} placeholder="char_conan_edogawa" /></label>
                  <label className="field">Display name<input required maxLength={255} value={bibleForm.display_name} onChange={(event) => setBibleForm({ ...bibleForm, display_name: event.target.value })} placeholder="Conan Edogawa" /></label>
                  <label className="field">Immutable traits<textarea rows={4} value={bibleForm.immutable_traits} onChange={(event) => setBibleForm({ ...bibleForm, immutable_traits: event.target.value })} placeholder="Các đặc điểm phải giữ nguyên giữa các shot…" /></label>
                  <label className="field">Mutable attributes <span className="field-hint">JSON object</span><textarea className="code-textarea" rows={5} spellCheck={false} value={bibleForm.mutable_attributes} onChange={(event) => setBibleForm({ ...bibleForm, mutable_attributes: event.target.value })} placeholder={'{"current_outfit":"signature outfit"}'} /></label>
                  <label className="field">Reference asset IDs <span className="field-hint">Phân cách bằng dấu phẩy</span><input value={bibleForm.reference_asset_ids} onChange={(event) => setBibleForm({ ...bibleForm, reference_asset_ids: event.target.value })} placeholder="asset-id-1, asset-id-2" /></label>
                  <label className="checkbox-field"><input type="checkbox" checked={bibleForm.requires_reference_lock} onChange={(event) => setBibleForm({ ...bibleForm, requires_reference_lock: event.target.checked })} /><span><strong>Require reference lock</strong><small>Shots có thể bị block khi asset bắt buộc chưa tồn tại.</small></span></label>
                  <button className="button button-primary button-full" type="submit" disabled={savingBible || !selectedProjectId}>{savingBible ? 'Đang lưu…' : 'Save & recompile prompts'}</button>
                </form>
              </section>
            </div>
          </>}
        </div>}

        {view === 'shots' && <div className="page-content">
          {!selectedProject ? <EmptyState icon="▤" title="Chưa có project" description="Shot Board cần project để hiển thị cảnh và prompt." actionLabel="Tạo project" onAction={() => { setView('projects'); setShowCreateProject(true); }} /> : <>
            <div className="stats-grid">
              <StatCard label="TOTAL SHOTS" value={String(shots.length).padStart(2, '0')} detail="Các shot trong sequence hiện tại" icon="▤" />
              <StatCard label="NEEDS REVIEW" value={String(shots.filter((shot) => shot.approval_state === 'needs_review' || shot.approval_state === 'draft').length).padStart(2, '0')} detail="Đang chờ đạo diễn duyệt" icon="◷" />
              <StatCard label="APPROVED" value={String(shots.filter((shot) => shot.approval_state === 'approved').length).padStart(2, '0')} detail="Đã qua approval gate" icon="✓" />
              <StatCard label="LINT WARNINGS" value={String(shots.reduce((sum, shot) => sum + (shot.lint_report || []).filter((issue) => issue.severity === 'warning').length, 0)).padStart(2, '0')} detail="Cần xem lại continuity & prompt" icon="△" />
            </div>
            {workspaceLoading ? <LoadingState label="Đang tải Shot Board…" /> : shots.length === 0 ? <EmptyState icon="▤" title="Shot Board chưa có dữ liệu" description="Import source script ở Script Studio rồi apply proposal để tạo scenes và shots." actionLabel="Mở Script Studio" onAction={() => setView('script')} /> :
              <div className="shot-workspace">
                <section className="panel shot-list-panel"><div className="section-heading inline-heading"><div><h2>Shot sequence</h2><p>{shots.length} shots · Sắp xếp theo sequence index</p></div><button type="button" className="button button-ghost button-compact" onClick={() => setView('script')}>＋ Import script</button></div>
                  <div className="shot-list">{shots.map((shot) => <button type="button" key={shot.id} className={'shot-row' + (shot.id === selectedShotId ? ' shot-row-active' : '')} onClick={() => setSelectedShotId(shot.id)}>
                    <span className="shot-index">{String(shot.sequence_index).padStart(2, '0')}</span>
                    <span className="shot-row-main"><strong>{shot.title || 'Shot ' + shot.sequence_index}</strong><small>{shot.location_code} · {shot.shot_size} · {shot.duration_seconds}s</small><span className="shot-row-action">{shot.action || 'Chưa mô tả action.'}</span></span>
                    <span className="shot-row-status"><span className={'status-pill status-' + toneForState(shot.approval_state)}>{labelForState(shot.approval_state)}</span><span className="shot-generation-state">{labelForState(shot.generation_state)}</span></span>
                  </button>)}</div>
                </section>
                {selectedShot && shotForm ? <section className="panel shot-detail-panel">
                  <div className="shot-detail-header"><div><span className="panel-kicker">SHOT {String(selectedShot.sequence_index).padStart(2, '0')} / DETAIL</span><h2>{selectedShot.title || 'Shot ' + selectedShot.sequence_index}</h2><code>{selectedShot.id}</code></div><span className={'status-pill status-' + toneForState(selectedShot.generation_state)}>{labelForState(selectedShot.generation_state)}</span></div>
                  <div className="shot-meta-strip"><span>⌂ {selectedShot.location_code}</span><span>◉ {(selectedShot.subject_codes || []).length} subjects</span><span>◷ {selectedShot.duration_seconds}s</span></div>
                  <form className="stack-form shot-edit-form" onSubmit={(event) => void handleSaveShot(event)}>
                    <label className="field">Shot size<select value={shotForm.shot_size} onChange={(event) => setShotForm({ ...shotForm, shot_size: event.target.value })}><option value="extreme_wide_shot">Extreme wide</option><option value="wide_shot">Wide shot</option><option value="medium_shot">Medium shot</option><option value="medium_close_up">Medium close-up</option><option value="close_up">Close-up</option><option value="extreme_close_up">Extreme close-up</option></select></label>
                    <label className="field">Camera motion<select value={shotForm.camera_motion} onChange={(event) => setShotForm({ ...shotForm, camera_motion: event.target.value })}><option value="static">Static</option><option value="slow_push_in">Slow push-in</option><option value="pan">Pan</option><option value="tilt">Tilt</option><option value="tracking">Tracking</option><option value="orbit">Orbit</option><option value="handheld">Handheld</option><option value="zoom_in">Zoom-in</option></select></label>
                    <label className="field">Composition<textarea rows={2} value={shotForm.composition} onChange={(event) => setShotForm({ ...shotForm, composition: event.target.value })} /></label>
                    <label className="field">Action<textarea rows={4} value={shotForm.action} onChange={(event) => setShotForm({ ...shotForm, action: event.target.value })} /></label>
                    <div className="form-grid form-grid-compact"><label className="field">Duration (seconds)<input type="number" min="0.1" max="300" step="0.1" value={shotForm.duration_seconds} onChange={(event) => setShotForm({ ...shotForm, duration_seconds: event.target.value })} /></label>
                      <label className="field">Approval state<select value={shotForm.approval_state} onChange={(event) => setShotForm({ ...shotForm, approval_state: event.target.value })}><option value="draft">Draft</option><option value="needs_review">Needs review</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select></label></div>
                    <button type="submit" className="button button-primary button-full" disabled={savingShot}>{savingShot ? 'Đang lưu…' : 'Save shot & re-run lint'}</button>
                  </form>
                  <div className="detail-divider" /><div className="detail-section-heading"><h3>Prompt preview</h3><span className="hash-chip">SHA {selectedShot.prompt_hash ? selectedShot.prompt_hash.slice(0, 10) : '—'}</span></div>
                  <pre className="prompt-preview">{selectedShot.prompt_final || 'Prompt sẽ xuất hiện sau khi pipeline biên dịch shot.'}</pre>
                  <div className="detail-section-heading lint-heading"><h3>Lint report</h3><span className="lint-count">{(selectedShot.lint_report || []).length} issues</span></div>
                  {(!selectedShot.lint_report || selectedShot.lint_report.length === 0) ? <div className="lint-empty"><span>✓</span> Không có issue nào được báo cáo.</div> :
                    <div className="lint-list">{selectedShot.lint_report.map((issue, index) => <div className={'lint-item lint-' + issue.severity} key={issue.code + '-' + index}>
                      <span className="lint-marker">{issue.severity === 'blocker' ? '!' : issue.severity === 'warning' ? '△' : 'i'}</span>
                      <div><strong>{issue.code}</strong><p>{issue.message}</p>{issue.field && <small>Field: {issue.field}</small>}</div><span className="lint-severity">{issue.severity}</span>
                    </div>)}</div>}
                  <div className="simulation-note"><span>ⓘ</span><p>Approval và lint là metadata của pipeline. Tính năng tạo video thật chưa được kết nối với production queue; không có render nào được chạy từ màn hình này.</p></div>
                </section> : <section className="panel shot-detail-panel"><EmptyState icon="▤" title="Chọn một shot" description="Chọn shot ở danh sách bên trái để xem prompt và cập nhật trạng thái." /></section>}
              </div>}
          </>}
        </div>}

        <footer className="page-footer"><span>MUSESTUDIO · M2 WORKSPACE</span><span>Script → Story Bible → Shot Board <i>·</i> Render queue chưa bật</span></footer>
      </main>

      {settingsOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSettingsOpen(false); }}>
        <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="settings-title">
          <div className="panel-heading"><div><span className="panel-kicker">CONNECTION SETTINGS</span><h2 id="settings-title">API & Access</h2></div><button type="button" className="icon-button" onClick={() => setSettingsOpen(false)} aria-label="Đóng">×</button></div>
          <form className="stack-form" onSubmit={(event) => { event.preventDefault(); setApiBaseUrl(apiBaseDraft.trim() || 'http://localhost:8000'); setAdminToken(adminTokenDraft); setSettingsOpen(false); setWorkspaceRefresh((count) => count + 1); notify('info', 'Đã áp dụng cấu hình kết nối. Đang kiểm tra lại API.'); }}>
            <label className="field">Orchestrator base URL<input required value={apiBaseDraft} onChange={(event) => setApiBaseDraft(event.target.value)} placeholder="http://localhost:8000" /></label>
            <label className="field">Studio Admin Token <span className="field-hint">Không bắt buộc ở dev mode</span><input type="password" autoComplete="off" value={adminTokenDraft} onChange={(event) => setAdminTokenDraft(event.target.value)} placeholder="Nhập token nếu API yêu cầu" /></label>
            <div className="security-note"><span>⌑</span><p>Token chỉ được giữ trong bộ nhớ phiên hiện tại, không ghi vào localStorage hoặc URL. Tải lại trang sẽ yêu cầu nhập lại. Không đưa secret vào source code hay file cấu hình frontend public.</p></div>
            <div className="form-actions"><button type="submit" className="button button-primary">Apply & reconnect</button><button type="button" className="button button-ghost" onClick={() => { setAdminTokenDraft(''); setAdminToken(''); setSettingsOpen(false); setWorkspaceRefresh((count) => count + 1); }}>Clear token</button></div>
          </form>
        </section>
      </div>}
    </div>
  );
};

function StatCard(props: { label: string; value: string; detail: string; icon: string }) {
  return <div className="stat-card"><div className="stat-card-top"><span>{props.label}</span><span className="stat-icon">{props.icon}</span></div><strong className="stat-value">{props.value}</strong><span className="stat-detail">{props.detail}</span></div>;
}
function LoadingState(props: { label: string }) {
  return <div className="loading-state"><span className="spinner" /><span>{props.label}</span></div>;
}
function EmptyState(props: { icon: string; title: string; description: string; actionLabel?: string; onAction?: () => void }) {
  return <div className="empty-state"><div className="empty-art">{props.icon}</div><h3>{props.title}</h3><p>{props.description}</p>{props.actionLabel && props.onAction && <button type="button" className="button button-secondary" onClick={props.onAction}>{props.actionLabel}</button>}</div>;
}
function PipelineStep(props: { number: string; title: string; description: string; state: 'done' | 'review' | 'pending' }) {
  return <div className={'pipeline-step step-' + props.state}><div className="pipeline-step-mark">{props.state === 'done' ? '✓' : props.state === 'review' ? '!' : props.number}</div><div><strong>{props.title}</strong><p>{props.description}</p></div><span className="pipeline-step-state">{props.state === 'done' ? 'DONE' : props.state === 'review' ? 'REVIEW' : 'NEXT'}</span></div>;
}

export default App;
