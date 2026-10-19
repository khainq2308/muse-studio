import type { ReactNode } from "react";
import type { Project, StudioApi } from "../services/api";

export type NoticeKind = "success" | "error" | "info";
export type Notify = (message: string, kind?: NoticeKind) => void;

export interface ModuleProps {
  api: StudioApi;
  project: Project;
  refreshToken: number;
  notify: Notify;
  onChanged: () => void;
}

export function PageHeader(props: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {props.eyebrow && <div className="eyebrow">{props.eyebrow}</div>}
        <h1>{props.title}</h1>
        <p>{props.description}</p>
      </div>
      {props.actions && <div className="heading-actions">{props.actions}</div>}
    </div>
  );
}

export function Panel(props: { title?: string; description?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${props.className ?? ""}`}>
      {(props.title || props.description || props.action) && (
        <div className="panel-heading">
          <div>
            {props.title && <h2>{props.title}</h2>}
            {props.description && <p>{props.description}</p>}
          </div>
          {props.action && <div>{props.action}</div>}
        </div>
      )}
      {props.children}
    </section>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <span className="empty-icon">✳</span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warning" | "bad" | "blue" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  let tone: "neutral" | "good" | "warning" | "bad" | "blue" = "neutral";
  if (["succeeded", "success", "healthy", "approved", "online", "completed"].includes(normalized)) tone = "good";
  else if (["running", "queued", "needs_review", "degraded", "blocked_upstream_frame"].includes(normalized)) tone = "blue";
  else if (["failed", "canceled", "cancelled", "auth_error", "unavailable", "blocked_reference"].includes(normalized)) tone = "bad";
  else if (["paused", "poll_timeout", "unknown", "draft"].includes(normalized)) tone = "warning";
  return <Badge tone={tone}>{status.replace(/_/g, " ")}</Badge>;
}

export function LoadingLine({ label = "Đang tải dữ liệu…" }: { label?: string }) {
  return <div className="loading-line"><span className="spinner" />{label}</div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="inline-error">
      <span>{message}</span>
      {onRetry && <button className="button button-quiet button-small" onClick={onRetry}>Thử lại</button>}
    </div>
  );
}
