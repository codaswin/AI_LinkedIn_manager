// Mirrors backend/app/main.py's response shapes exactly. Kept as one small
// hand-written file rather than a generated client — the API surface is
// small (8 resources) and stable enough that codegen would be more
// machinery than the problem needs.

export type ApprovalStatus = "pending" | "executing" | "succeeded" | "failed" | "rejected";

export type DashboardRole = "viewer" | "operator" | "admin";

export interface DashboardSession {
  user: { id: string; username: string; role: DashboardRole };
  csrf_token: string;
}

export interface ApprovalRequest {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  requested_by_agent: string;
  reason: string;
  confidence: number | null;
  status: ApprovalStatus;
  idempotency_key: string;
  attempt_count: number;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  execution_started_at: string | null;
  execution_finished_at: string | null;
  last_error: string | null;
}

export type LearningProposalStatus = "pending" | "approved" | "rejected" | "auto_applied";

export interface LearningProposal {
  id: string;
  pattern: string;
  change_type: string;
  proposed_change: string;
  confidence: number;
  status: LearningProposalStatus;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

export interface SettingValue {
  key: string;
  value: string;
  updated_by?: string;
  updated_at?: string;
}

export interface CostSummary {
  today_usd: number;
  budget_usd: number;
}

export interface ReflectionResult {
  ran: boolean;
  reason?: string;
  feedback_count: number;
  proposal_count?: number;
  proposals: LearningProposal[];
}

export interface ApiErrorBody {
  detail: string;
}

// GET /activity returns null when no workflow is currently running —
// app.activity's stack-based board reports its top entry, or null if empty.
export interface AgentActivity {
  agent: string;
  action: string;
  detail: string;
  source: string | null;
  elapsed_seconds: number;
}

// -- Brand voice --------------------------------------------------------

export interface BrandVoice {
  id: string;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
}

// -- Manual workflow triggers ---------------------------------------------
// Each trigger's result shape varies by which branch the underlying agent
// function took (e.g. write_post returns a different shape for
// needs_human_rewrite vs. submitted_for_approval) — kept as a loose record
// and rendered generically rather than over-fitting a union type to
// internals that may change independently of this UI.
export type WorkflowResult = Record<string, unknown>;

// Mirrors research_pipeline._to_normalized_dict()'s flat shape exactly.
export interface ResearchResultItem {
  source: string;
  title: string;
  summary: string;
  url: string;
  author: string;
  published_at: string;
  score: number | null;
  metadata: Record<string, unknown>;
}

// -- Connections (platform credentials) ------------------------------------
// Mirrors memory/platform_credentials.py's list_platform_status() shape.
// The API never returns a raw secret value — only a masked preview (saved
// through this UI) or a plain status label (already set on the server).

export type CredentialFieldStatus = "not_set" | "saved_here" | "set_on_server";

export interface CredentialFieldStatusInfo {
  name: string;
  label: string;
  secret: boolean;
  placeholder: string;
  status: CredentialFieldStatus;
  masked_preview: string | null;
}

export type CredentialType = "api_key" | "token" | "oauth_connected_account" | "client_credentials" | "endpoint";

export interface PlatformCredentialStatus {
  id: string;
  name: string;
  group: string;
  credential_type: CredentialType;
  summary: string;
  help_text: string;
  required: boolean;
  connected: boolean;
  fields: CredentialFieldStatusInfo[];
}

// approval_gate.approve() returns the gated tool's raw execution result
// (tools.sandbox.ToolExecutionResult), not an ApprovalRequest — asymmetric
// with reject(), which does return the ApprovalRequest. Both are real API
// shapes, not a frontend inconsistency.
export interface ToolExecutionResult {
  tool_name?: string;
  status: "success" | "error" | "timeout" | "blocked";
  result?: Record<string, unknown> | null;
  error?: string | null;
  latency_seconds?: number;
}

// -- Admin: dashboard users ------------------------------------------------
// Invite-only account creation — GET/POST /admin/users, admin role required.
// Never carries a password or password hash.

export interface DashboardUser {
  id: string;
  username: string;
  role: DashboardRole;
  active: boolean;
  created_at: string;
  last_login_at: string | null;
}
