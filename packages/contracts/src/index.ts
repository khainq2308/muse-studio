export type ShotApprovalState = 'draft' | 'needs_review' | 'approved' | 'rejected';

export type ShotGenerationState =
  | 'not_started'
  | 'blocked_reference'
  | 'blocked_upstream_frame'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled';

export type NodeExecutionState =
  | 'idle'
  | 'ready'
  | 'blocked'
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'skipped';

export type ConnectionHealthState = 'unknown' | 'healthy' | 'degraded' | 'auth_error' | 'unavailable';
