import { useEffect, useState, type FormEvent } from "react";
import type { Connection, ConnectionAccount } from "../services/api";
import type { ModuleProps } from "./shared";
import { Badge, EmptyState, ErrorState, LoadingLine, PageHeader, Panel, StatusBadge } from "./shared";

export default function Connections({ api, refreshToken, notify, onChanged }: ModuleProps) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [accounts, setAccounts] = useState<ConnectionAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [accountsLoading, setAccountsLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showConnectionForm, setShowConnectionForm] = useState(false);
  const [connectionForm, setConnectionForm] = useState({
    name: "",
    provider_type: "muse2api" as "muse2api" | "mock",
    base_url: "https://your-muse2api-host",
    api_key: "",
    timeout_seconds: 30,
    max_concurrency: 2,
  });
  const [accountForm, setAccountForm] = useState({ label: "", api_key: "", proxy_url: "", priority: 100 });

  async function loadConnections() {
    setLoading(true);
    setError("");
    try {
      const rows = await api.listConnections();
      setConnections(rows);
      const nextSelected = rows.some((connection) => connection.id === selectedId)
        ? selectedId
        : (rows[0]?.id ?? "");
      setSelectedId(nextSelected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không tải được connections.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { void loadConnections(); }, [refreshToken]);

  const selected = connections.find((connection) => connection.id === selectedId) ?? null;
  useEffect(() => {
    if (!selectedId) {
      setAccounts([]);
      return;
    }
    let cancelled = false;
    setAccountsLoading(true);
    api.listAccounts(selectedId).then((rows) => {
      if (!cancelled) setAccounts(rows);
    }).catch((err: unknown) => {
      if (!cancelled) {
        setAccounts([]);
        notify(err instanceof Error ? err.message : "Không tải được account pool.", "error");
      }
    }).finally(() => {
      if (!cancelled) setAccountsLoading(false);
    });
    return () => { cancelled = true; };
  }, [api, selectedId, refreshToken]);

  async function createConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await api.createConnection({
        ...connectionForm,
        name: connectionForm.name.trim(),
        base_url: connectionForm.base_url.trim(),
        api_key: connectionForm.api_key.trim() || undefined,
        enabled: true,
      });
      setConnections((current) => [created, ...current.filter((connection) => connection.id !== created.id)]);
      setSelectedId(created.id);
      setConnectionForm({ name: "", provider_type: "muse2api", base_url: "https://your-muse2api-host", api_key: "", timeout_seconds: 30, max_concurrency: 2 });
      setShowConnectionForm(false);
      notify("Đã tạo connection.", "success");
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không tạo được connection.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    try {
      const account = await api.createAccount(selected.id, {
        label: accountForm.label.trim(),
        api_key: accountForm.api_key,
        proxy_url: accountForm.proxy_url.trim() || undefined,
        enabled: true,
        priority: accountForm.priority,
      });
      setAccounts((current) => [account, ...current.filter((row) => row.id !== account.id)]);
      setAccountForm({ label: "", api_key: "", proxy_url: "", priority: 100 });
      notify("Đã thêm account vào pool.", "success");
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không thêm được account.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    if (!selected) return;
    setBusy(true);
    try {
      const tested = await api.testConnection(selected.id);
      setConnections((current) => current.map((connection) => connection.id === tested.id ? tested : connection));
      notify(`Probe hoàn tất: ${tested.health_state}.`, tested.health_state === "healthy" ? "success" : "info");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Probe connection thất bại.", "error");
      await loadConnections();
    } finally {
      setBusy(false);
    }
  }

  async function testAccount(account: ConnectionAccount) {
    if (!selected) return;
    setBusy(true);
    try {
      const tested = await api.testAccount(selected.id, account.id);
      setAccounts((current) => current.map((row) => row.id === tested.id ? tested : row));
      notify(`Account probe: ${tested.health_state}.`, tested.health_state === "healthy" ? "success" : "info");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Probe account thất bại.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function deleteAccount(account: ConnectionAccount) {
    if (!selected || !window.confirm(`Xóa account “${account.label}” khỏi pool? Các task đang chạy có thể phụ thuộc account này.`)) return;
    setBusy(true);
    try {
      await api.deleteAccount(selected.id, account.id);
      setAccounts((current) => current.filter((row) => row.id !== account.id));
      notify("Đã xóa account.", "success");
      onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Không xóa được account.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="module-stack">
      <PageHeader
        eyebrow="M1 / M7 · PROVIDERS"
        title="Connections & Accounts"
        description="Quản lý endpoint provider, kiểm tra health và quản lý pool API keys/proxy theo từng account."
        actions={<button className="button button-primary" onClick={() => setShowConnectionForm((current) => !current)}>＋ New connection</button>}
      />
      {showConnectionForm && (
        <Panel title="Tạo provider connection" description="API key ở cấp connection là tùy chọn; account pool cho phép cấu hình key và proxy riêng từng tài khoản.">
          <form className="form-stack" onSubmit={(event) => { void createConnection(event); }}>
            <div className="field-grid-2">
              <label className="field"><span>Name</span><input required maxLength={128} value={connectionForm.name} onChange={(event) => setConnectionForm({ ...connectionForm, name: event.target.value })} placeholder="My muse2api" /></label>
              <label className="field"><span>Provider type</span><select value={connectionForm.provider_type} onChange={(event) => setConnectionForm({ ...connectionForm, provider_type: event.target.value as "muse2api" | "mock" })}><option value="muse2api">muse2api</option><option value="mock">mock</option></select></label>
            </div>
            <label className="field"><span>Base URL</span><input required minLength={4} maxLength={512} value={connectionForm.base_url} onChange={(event) => setConnectionForm({ ...connectionForm, base_url: event.target.value })} placeholder="https://api.example.com" /></label>
            <label className="field"><span>Connection-level API key (optional)</span><input type="password" autoComplete="new-password" value={connectionForm.api_key} onChange={(event) => setConnectionForm({ ...connectionForm, api_key: event.target.value })} placeholder="Để trống nếu dùng account pool" /></label>
            <div className="field-grid-2">
              <label className="field"><span>Timeout (5–120s)</span><input type="number" min={5} max={120} value={connectionForm.timeout_seconds} onChange={(event) => setConnectionForm({ ...connectionForm, timeout_seconds: Number(event.target.value) })} /></label>
              <label className="field"><span>Max concurrency (1–20)</span><input type="number" min={1} max={20} value={connectionForm.max_concurrency} onChange={(event) => setConnectionForm({ ...connectionForm, max_concurrency: Number(event.target.value) })} /></label>
            </div>
            <div className="editor-footer"><button type="button" className="button button-quiet" onClick={() => setShowConnectionForm(false)}>Hủy</button><button className="button button-primary" disabled={busy}>{busy ? "Đang tạo…" : "Tạo connection"}</button></div>
          </form>
        </Panel>
      )}

      <div className="connections-layout">
        <Panel title="Provider connections" description={`${connections.length} endpoint(s)`} className="connection-list-panel">
          {loading ? <LoadingLine /> : error ? <ErrorState message={error} onRetry={() => { void loadConnections(); }} /> : connections.length === 0 ? (
            <EmptyState title="Chưa có connection" detail="Tạo connection để có thể enqueue generation task." />
          ) : (
            <div className="connection-list">
              {connections.map((connection) => (
                <button key={connection.id} className={`connection-list-item ${connection.id === selectedId ? "is-selected" : ""}`} onClick={() => setSelectedId(connection.id)}>
                  <span className="connection-icon">{connection.provider_type === "mock" ? "◈" : "↗"}</span>
                  <span className="connection-list-copy"><strong>{connection.name}</strong><small>{connection.base_url}</small><small>{connection.api_key_masked}</small></span>
                  <StatusBadge status={connection.enabled ? connection.health_state : "disabled"} />
                </button>
              ))}
            </div>
          )}
        </Panel>

        <div className="module-stack">
          {!selected ? <Panel><EmptyState title="Chọn một connection" detail="Thông tin health và account pool sẽ xuất hiện ở đây." /></Panel> : (
            <>
              <Panel title={selected.name} description={selected.base_url} action={<button className="button button-secondary button-small" disabled={busy} onClick={() => { void testConnection(); }}>Test connection</button>}>
                <div className="detail-chip-row"><StatusBadge status={selected.health_state} /><Badge tone={selected.enabled ? "good" : "bad"}>{selected.enabled ? "enabled" : "disabled"}</Badge></div>
                <div className="detail-list">
                  <div><span>Provider</span><strong>{selected.provider_type}</strong></div>
                  <div><span>Masked API key</span><strong>{selected.api_key_masked}</strong></div>
                  <div><span>Proxy display host</span><strong>{selected.proxy_display_host || "Not set"}</strong></div>
                  <div><span>Timeout</span><strong>{selected.timeout_seconds}s</strong></div>
                  <div><span>Concurrency cap</span><strong>{selected.max_concurrency}</strong></div>
                  <div><span>Latency</span><strong>{selected.latency_ms == null ? "—" : `${selected.latency_ms} ms`}</strong></div>
                </div>
                {selected.last_error_redacted && <div className="callout callout-warning"><strong>Latest redacted error</strong><p>{selected.last_error_redacted}</p></div>}
              </Panel>
              <Panel title="Account pool" description="Mỗi account có API key được mã hóa và proxy riêng, nếu được cấu hình.">
                {accountsLoading ? <LoadingLine /> : accounts.length === 0 ? <EmptyState title="Chưa có account" detail="Thêm một hoặc nhiều API keys trong pool để điều phối quota theo account." /> : (
                  <div className="account-list">
                    {accounts.map((account) => (
                      <div className="account-row" key={account.id}>
                        <div className="account-avatar">A</div>
                        <div className="account-copy"><strong>{account.label}</strong><small>{account.api_key_masked} · priority {account.priority}</small><small>{account.proxy_display_host || (account.has_proxy ? "Proxy configured" : "No proxy")} · last used {account.last_used_at ? new Date(account.last_used_at).toLocaleString() : "never"}</small>{account.last_error_redacted && <small className="error-text">{account.last_error_redacted}</small>}</div>
                        <div className="account-actions"><StatusBadge status={account.enabled ? account.health_state : "disabled"} /><button className="button button-quiet button-small" disabled={busy} onClick={() => { void testAccount(account); }}>Test</button><button className="button button-danger button-small" disabled={busy} onClick={() => { void deleteAccount(account); }}>Delete</button></div>
                      </div>
                    ))}
                  </div>
                )}
                <details className="add-account-details">
                  <summary>＋ Thêm account</summary>
                  <form className="form-stack add-account-form" onSubmit={(event) => { void createAccount(event); }}>
                    <label className="field"><span>Label</span><input required maxLength={128} value={accountForm.label} onChange={(event) => setAccountForm({ ...accountForm, label: event.target.value })} placeholder="account-01" /></label>
                    <label className="field"><span>API key</span><input type="password" required autoComplete="new-password" value={accountForm.api_key} onChange={(event) => setAccountForm({ ...accountForm, api_key: event.target.value })} /></label>
                    <label className="field"><span>Proxy URL (optional)</span><input value={accountForm.proxy_url} onChange={(event) => setAccountForm({ ...accountForm, proxy_url: event.target.value })} placeholder="socks5://user:pass@host:port" /></label>
                    <label className="field"><span>Priority</span><input type="number" min={0} max={10000} value={accountForm.priority} onChange={(event) => setAccountForm({ ...accountForm, priority: Number(event.target.value) })} /></label>
                    <button className="button button-primary full-width" disabled={busy}>{busy ? "Đang thêm…" : "Add account to pool"}</button>
                    <p className="muted-text">Secret được gửi qua HTTPS trong production và không được ghi vào trình duyệt lưu trữ lâu dài. Không chia sẻ screenshot chứa key hoặc proxy credentials.</p>
                  </form>
                </details>
              </Panel>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
