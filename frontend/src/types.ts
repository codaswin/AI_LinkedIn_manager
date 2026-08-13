// Mirrors backend/app/main.py's response shapes exactly. Kept as one small
// hand-written file rather than a generated client — the API surface is
// small (8 resources) and stable enough that codegen would be more
// machinery than the problem needs.

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface ApprovalRequest {
  id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  requested_by_agent: string;
  reason: string;
  confidence: number | null;
  status: ApprovalStatus;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
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
