import React, { useEffect, useState } from 'react';

interface Project {
  id: string;
  name: string;
  aspect_ratio: string;
  fps: number;
  style_preset: string;
  current_revision_num: number;
  asset_count: number;
}

interface Connection {
  id: string;
  name: string;
  provider_type: string;
  base_url: string;
  health_state: string;
  latency_ms: number | null;
}

export const App: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [apiStatus, setApiStatus] = useState<string>('checking...');
  const [draftAdminToken, setDraftAdminToken] = useState<string>('');
  const [activeAdminToken, setActiveAdminToken] = useState<string>('');
  const orchestratorUrl = (import.meta as any).env?.VITE_ORCHESTRATOR_URL || 'http://localhost:8000';
  const requestHeaders: HeadersInit = activeAdminToken
    ? { 'X-Studio-Admin-Token': activeAdminToken }
    : {};

  useEffect(() => {
    fetch(`${orchestratorUrl}/healthz`)
      .then((res) => res.json())
      .then((data) => setApiStatus(data.status === 'ok' ? 'Online (M1 Active)' : 'Unknown'))
      .catch(() => setApiStatus('Offline (Start Orchestrator)'));

    fetch(`${orchestratorUrl}/api/v1/projects`, { headers: requestHeaders })
      .then((res) => {
        if (res.status === 401 || res.status === 403) throw new Error('Admin token required');
        if (!res.ok) throw new Error(`Projects API failed (${res.status})`);
        return res.json();
      })
      .then((data) => setProjects(Array.isArray(data) ? data : []))
      .catch((error) => {
        setProjects([]);
        if (error instanceof Error && error.message === 'Admin token required') {
          setApiStatus('Admin token required');
        }
      });

    fetch(`${orchestratorUrl}/api/v1/connections`, { headers: requestHeaders })
      .then((res) => {
        if (res.status === 401 || res.status === 403) throw new Error('Admin token required');
        if (!res.ok) throw new Error(`Connections API failed (${res.status})`);
        return res.json();
      })
      .then((data) => setConnections(Array.isArray(data) ? data : []))
      .catch(() => setConnections([]));
  }, [orchestratorUrl, activeAdminToken]);

  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif', padding: '2rem', backgroundColor: '#0f172a', color: '#f8fafc', minHeight: '100vh' }}>
      <header style={{ borderBottom: '1px solid #334155', paddingBottom: '1.5rem', marginBottom: '2rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: '1.875rem', fontWeight: 'bold', margin: 0, color: '#38bdf8' }}>MuseStudio</h1>
          <p style={{ margin: '0.25rem 0 0 0', color: '#94a3b8' }}>AI Video Production Orchestrator — Milestone 1 (Foundation)</p>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setActiveAdminToken(draftAdminToken.trim());
            }}
            style={{ display: 'flex', gap: '0.375rem', alignItems: 'center' }}
          >
            <input
              aria-label="Studio admin token"
              type="password"
              autoComplete="current-password"
              placeholder="Admin token (production)"
              value={draftAdminToken}
              onChange={(event) => setDraftAdminToken(event.target.value)}
              style={{ width: '13rem', padding: '0.5rem', borderRadius: '0.375rem', background: '#0f172a', color: '#f8fafc', border: '1px solid #334155' }}
            />
            <button
              type="submit"
              style={{ padding: '0.5rem 0.75rem', borderRadius: '0.375rem', border: '1px solid #334155', background: '#1e293b', color: '#f8fafc', cursor: 'pointer' }}
            >Connect</button>
            {activeAdminToken && (
              <button
                type="button"
                onClick={() => { setDraftAdminToken(''); setActiveAdminToken(''); }}
                style={{ padding: '0.5rem 0.75rem', borderRadius: '0.375rem', border: '1px solid #334155', background: '#1e293b', color: '#f8fafc', cursor: 'pointer' }}
              >Disconnect</button>
            )}
          </form>
          <div style={{ background: '#1e293b', padding: '0.5rem 1rem', borderRadius: '0.5rem', border: '1px solid #334155' }}>
            <span style={{ fontSize: '0.875rem', color: '#94a3b8' }}>API Status: </span>
            <span style={{ fontSize: '0.875rem', fontWeight: 600, color: apiStatus.includes('Online') ? '#4ade80' : '#f87171' }}>{apiStatus}</span>
          </div>
        </div>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem' }}>
        {/* Project Foundation Card */}
        <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '0.75rem', padding: '1.5rem' }}>
          <h2 style={{ fontSize: '1.25rem', marginTop: 0, color: '#f1f5f9' }}>🎬 Projects ({projects.length})</h2>
          {projects.length === 0 ? (
            <p style={{ color: '#64748b', fontSize: '0.9rem' }}>No projects yet. Use API to create revisioned projects.</p>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {projects.map((p) => (
                <li key={p.id} style={{ background: '#0f172a', padding: '0.75rem', borderRadius: '0.375rem', marginBottom: '0.5rem', border: '1px solid #334155' }}>
                  <strong>{p.name}</strong> <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>({p.aspect_ratio}, {p.fps}fps)</span>
                  <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Rev #{p.current_revision_num} • {p.asset_count} assets</div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Connection Profiles Card */}
        <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '0.75rem', padding: '1.5rem' }}>
          <h2 style={{ fontSize: '1.25rem', marginTop: 0, color: '#f1f5f9' }}>🔌 Provider Connections ({connections.length})</h2>
          {connections.length === 0 ? (
            <p style={{ color: '#64748b', fontSize: '0.9rem' }}>No connections registered. Supports muse2api & mock.</p>
          ) : (
            <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
              {connections.map((c) => (
                <li key={c.id} style={{ background: '#0f172a', padding: '0.75rem', borderRadius: '0.375rem', marginBottom: '0.5rem', border: '1px solid #334155' }}>
                  <strong>{c.name}</strong> <span style={{ color: '#38bdf8', fontSize: '0.8rem' }}>[{c.provider_type}]</span>
                  <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Status: {c.health_state} • {c.latency_ms ?? 0}ms</div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Milestones Roadmap Card */}
        <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '0.75rem', padding: '1.5rem' }}>
          <h2 style={{ fontSize: '1.25rem', marginTop: 0, color: '#f1f5f9' }}>🗺️ Roadmap Progress</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.875rem' }}>
            <div style={{ color: '#4ade80' }}>✔ M0: Audit, Capability Matrix & Contracts</div>
            <div style={{ color: '#4ade80' }}>✔ M1: Project Foundation, Vault & Asset Store</div>
            <div style={{ color: '#38bdf8', fontWeight: 'bold' }}>➔ M2: Script Studio, Story Bible & Shot Board</div>
            <div style={{ color: '#64748b' }}>○ M3: Workflow Canvas & Typed DAG Execution</div>
            <div style={{ color: '#64748b' }}>○ M4: Live muse2api Polling Queue Worker</div>
            <div style={{ color: '#64748b' }}>○ M5: Batch Render & Takes A/B Review</div>
            <div style={{ color: '#64748b' }}>○ M6: Timeline Editor & FFmpeg MP4 Export</div>
          </div>
        </div>
      </div>
    </div>
  );
};
export default App;
