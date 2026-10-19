import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { createStudioApi, type Project } from "./services/api";
import Overview from "./modules/Overview";
import ScriptStudio from "./modules/ScriptStudio";
import StoryBible from "./modules/StoryBible";
import ShotBoard from "./modules/ShotBoard";
import QueueMonitor from "./modules/QueueMonitor";
import Connections from "./modules/Connections";
import Timeline from "./modules/Timeline";
import type { NoticeKind } from "./modules/shared";
import "./styles.css";

type ModuleId = "overview" | "script" | "bible" | "shots" | "queue" | "connections" | "timeline";

const NAVIGATION: Array<{ id: ModuleId; label: string; description: string; icon: string; group: string }> = [
  { id: "overview", label: "Workspace", description: "Tổng quan project", icon: "◫", group: "WORKSPACE" },
  { id: "script", label: "Script Studio", description: "Kịch bản → shot", icon: "Aa", group: "CREATE" },
  { id: "bible", label: "Story Bible", description: "Character & location locks", icon: "◈", group: "CREATE" },
  { id: "shots", label: "Shot Board", description: "Prompt, lint & takes", icon: "▤", group: "CREATE" },
  { id: "queue", label: "Queue Monitor", description: "Generation jobs", icon: "⇄", group: "PRODUCTION" },
  { id: "timeline", label: "Timeline & Export", description: "Assemble & render", icon: "▥", group: "PRODUCTION" },
  { id: "connections", label: "Connections", description: "Provider & account pool", icon: "⌁", group: "SETTINGS" },
];

const MODULE_COPY: Record<ModuleId, string> = Object.fromEntries(
  NAVIGATION.map((item) => [item.id, item.description]),
) as Record<ModuleId, string>;

export const App = () => {
  const [adminToken, setAdminToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [activeModule, setActiveModule] = useState<ModuleId>("overview");
  const [apiState, setApiState] = useState<"checking" | "online" | "auth" | "offline">("checking");
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [projectError, setProjectError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [notice, setNotice] = useState<{ message: string; kind: NoticeKind } | null>(null);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectDescription, setNewProjectDescription] = useState("");
  const [newProjectAspect, setNewProjectAspect] = useState("16:9");
  const [creatingProject, setCreatingProject] = useState(false);
  const api = useMemo(() => createStudioApi(adminToken), [adminToken]);

  const notify = useCallback((message: string, kind: NoticeKind = "info") => {
    setNotice({ message, kind });
  }, []);
  const onChanged = useCallback(() => setRefreshToken((current) => current + 1), []);

  async function loadProjects() {
    setLoadingProjects(true);
    setProjectError("");
    try {
      const [rows, health] = await Promise.all([
        api.listProjects(),
        api.request<{ status?: string }>("/healthz").catch(() => ({ status: "unknown" })),
      ]);
      setProjects(rows);
      setApiState(health.status === "ok" ? "online" : "online");
      if (rows.length > 0 && !rows.some((project) => project.id === selectedProjectId)) {
        setSelectedProjectId(rows[0].id);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Không tải được projects.";
      setProjectError(message);
      setApiState(message.includes("401") || message.includes("403") || /token|unauthorized|forbidden/i.test(message) ? "auth" : "offline");
    } finally {
      setLoadingProjects(false);
    }
  }

  useEffect(() => {
    let live = true;
    setLoadingProjects(true);
    setProjectError("");
    Promise.all([
      api.listProjects(),
      api.request<{ status?: string }>("/healthz").catch(() => ({ status: "unknown" })),
    ]).then(([rows, health]) => {
      if (!live) return;
      setProjects(rows);
      setApiState(health.status === "ok" ? "online" : "online");
      setSelectedProjectId((current) => rows.some((project) => project.id === current) ? current : (rows[0]?.id ?? ""));
    }).catch((err: unknown) => {
      if (!live) return;
      const message = err instanceof Error ? err.message : "Không thể kết nối API.";
      setProjectError(message);
      setApiState(message.includes("401") || message.includes("403") || /token|unauthorized|forbidden/i.test(message) ? "auth" : "offline");
    }).finally(() => {
      if (live) setLoadingProjects(false);
    });
    return () => { live = false; };
  }, [api]);

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null;

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newProjectName.trim();
    if (name.length === 0) {
      notify("Nhập tên project trước khi tạo.", "error");
      return;
    }
    setCreatingProject(true);
    try {
      const project = await api.createProject({
        name,
        description: newProjectDescription.trim(),
        aspect_ratio: newProjectAspect,
        fps: 24,
        style_preset: "2d_anime_cinematic",
        language: "vi",
        output_profile: "1080p_h264",
      });
      setProjects((current) => [project, ...current.filter((item) => item.id !== project.id)]);
      setSelectedProjectId(project.id);
      setNewProjectName("");
      setNewProjectDescription("");
      notify("Project đã được tạo.", "success");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không tạo được project.", "error");
    } finally {
      setCreatingProject(false);
    }
  }

  function connectToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAdminToken(draftToken.trim());
    setDraftToken("");
    notify("Đang kiểm tra token với Orchestrator…", "info");
  }

  function disconnectToken() {
    setAdminToken("");
    setDraftToken("");
    notify("Đã xóa token khỏi bộ nhớ phiên hiện tại.", "info");
  }

  function renderModule() {
    if (!selectedProject) return null;
    const props = { api, project: selectedProject, refreshToken, notify, onChanged };
    switch (activeModule) {
      case "script": return <ScriptStudio {...props} />;
      case "bible": return <StoryBible {...props} />;
      case "shots": return <ShotBoard {...props} />;
      case "queue": return <QueueMonitor {...props} />;
      case "connections": return <Connections {...props} />;
      case "timeline": return <Timeline {...props} />;
      case "overview":
      default: return <Overview {...props} />;
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? "sidebar-open" : ""}`}>
        <div className="brand-block">
          <div className="brand-mark"><span>M</span><i /></div>
          <div className="brand-name"><strong>Muse<span>Studio</span></strong><small>AI FILM WORKSPACE</small></div>
          <button className="mobile-close" aria-label="Close navigation" onClick={() => setMobileNavOpen(false)}>×</button>
        </div>
        <div className="sidebar-project">
          <span className="eyebrow">CURRENT PROJECT</span>
          <div className="project-selector-wrap">
            <span className="project-dot" />
            <select aria-label="Current project" value={selectedProjectId} onChange={(event) => { setSelectedProjectId(event.target.value); setMobileNavOpen(false); }} disabled={projects.length === 0}>
              {projects.length === 0 ? <option value="">No project selected</option> : projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
            </select>
          </div>
          {selectedProject && <small className="sidebar-project-meta">{selectedProject.aspect_ratio} · {selectedProject.fps} FPS · v{selectedProject.current_revision_num}</small>}
        </div>

        <nav className="nav-list" aria-label="Workspace modules">
          {(["WORKSPACE", "CREATE", "PRODUCTION", "SETTINGS"] as const).map((group) => (
            <div className="nav-group" key={group}>
              <span className="nav-group-title">{group}</span>
              {NAVIGATION.filter((item) => item.group === group).map((item) => (
                <button
                  key={item.id}
                  className={`nav-item ${activeModule === item.id ? "nav-item-active" : ""}`}
                  onClick={() => { setActiveModule(item.id); setMobileNavOpen(false); }}
                  title={item.description}
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span className="nav-item-copy"><strong>{item.label}</strong><small>{item.description}</small></span>
                  {activeModule === item.id && <span className="nav-active-dot" />}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-status"><span className={`status-dot status-${apiState}`} /><div><strong>{apiState === "online" ? "Orchestrator online" : apiState === "auth" ? "Token required" : apiState === "checking" ? "Connecting…" : "API unavailable"}</strong><small>{apiState === "online" ? "Connected to workspace API" : "Check local API or credentials"}</small></div></div>
          <div className="sidebar-footer"><span>STUDIO WEB</span><span>v0.2 · workspace</span></div>
        </div>
      </aside>

      {mobileNavOpen && <button className="mobile-backdrop" aria-label="Close menu" onClick={() => setMobileNavOpen(false)} />}

      <main className="main-shell">
        <header className="topbar">
          <div className="topbar-left">
            <button className="mobile-menu-button" aria-label="Open navigation" onClick={() => setMobileNavOpen(true)}>☰</button>
            <div className="breadcrumb"><span>MuseStudio</span><b>/</b><strong>{NAVIGATION.find((item) => item.id === activeModule)?.label ?? activeModule}</strong></div>
          </div>
          <div className="topbar-right">
            <div className={`api-pill api-pill-${apiState}`}><span />{apiState === "online" ? "API connected" : apiState === "auth" ? "Auth required" : apiState === "checking" ? "Checking API" : "API offline"}</div>
            <form className="token-form" onSubmit={connectToken}>
              <input aria-label="Admin token" type="password" autoComplete="current-password" placeholder={adminToken ? "Token connected" : "Studio admin token"} value={draftToken} onChange={(event) => setDraftToken(event.target.value)} />
              <button className="button button-small button-secondary" type="submit">{adminToken ? "Update token" : "Connect"}</button>
              {adminToken && <button className="button button-small button-quiet" type="button" onClick={disconnectToken} title="Disconnect token">×</button>}
            </form>
          </div>
        </header>

        <section className="page-container">
          {loadingProjects ? (
            <div className="center-stage"><span className="spinner spinner-large" /><strong>Connecting to MuseStudio…</strong><p>Reading projects and service health.</p></div>
          ) : projectError && apiState !== "online" ? (
            <div className="center-stage center-stage-error">
              <div className="hero-symbol">↯</div>
              <span className="eyebrow">CONNECTION REQUIRED</span>
              <h1>{apiState === "auth" ? "Cần kết nối token" : "Không kết nối được Orchestrator"}</h1>
              <p>{projectError}</p>
              <p className="muted-text">Hãy kiểm tra API đang chạy và URL VITE_ORCHESTRATOR_URL. Với môi trường có admin auth, nhập Studio admin token ở thanh trên.</p>
              <button className="button button-primary" onClick={() => { void loadProjects(); }}>Thử kết nối lại</button>
            </div>
          ) : projects.length === 0 ? (
            <div className="center-stage create-project-stage">
              <div className="hero-symbol">✳</div>
              <span className="eyebrow">START A NEW PRODUCTION</span>
              <h1>Tạo project đầu tiên</h1>
              <p>Tổ chức kịch bản, Story Bible, shot generation, takes và timeline trong một workspace.</p>
              <form className="new-project-form" onSubmit={(event) => { void createProject(event); }}>
                <label className="field"><span>Tên project</span><input required maxLength={255} autoFocus value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="Ví dụ: Conan — Episode 01" /></label>
                <label className="field"><span>Mô tả (tuỳ chọn)</span><textarea rows={2} maxLength={2000} value={newProjectDescription} onChange={(event) => setNewProjectDescription(event.target.value)} placeholder="Mục tiêu, bối cảnh, ghi chú production…" /></label>
                <label className="field"><span>Aspect ratio</span><select value={newProjectAspect} onChange={(event) => setNewProjectAspect(event.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option><option>21:9</option></select></label>
                <button className="button button-primary full-width" disabled={creatingProject}>{creatingProject ? "Đang tạo…" : "Create project →"}</button>
              </form>
            </div>
          ) : selectedProject ? (
            <>
              <div className="workspace-context">
                <div><span className="project-context-dot" /><span>{selectedProject.name}</span><span className="context-divider">/</span><span>{MODULE_COPY[activeModule]}</span></div>
                <div className="context-meta"><span>{selectedProject.aspect_ratio}</span><span>{selectedProject.fps} FPS</span><span>Revision {selectedProject.current_revision_num}</span></div>
              </div>
              {renderModule()}
            </>
          ) : (
            <div className="center-stage"><h1>Chọn project</h1><p>Chọn một project từ thanh bên để mở workspace.</p></div>
          )}
        </section>
      </main>

      {notice && (
        <div className={`toast toast-${notice.kind}`} role="status">
          <span className="toast-icon">{notice.kind === "success" ? "✓" : notice.kind === "error" ? "!" : "i"}</span>
          <span>{notice.message}</span>
          <button aria-label="Dismiss notice" onClick={() => setNotice(null)}>×</button>
        </div>
      )}
    </div>
  );
};

export default App;
