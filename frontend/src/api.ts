import type {
  ApiErrorBody,
  ApprovalRequest,
  CostSummary,
  LearningProposal,
  ReflectionResult,
  SettingValue,
  ToolExecutionResult,
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({ detail: response.statusText }))) as ApiErrorBody;
    throw new ApiError(response.status, body.detail ?? `Request to ${path} failed with ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

// -- Health -------------------------------------------------------------

export const getHealth = (): Promise<{ status: string }> => request("/health");

// -- Settings -------------------------------------------------------------

export const getSetting = (key: string): Promise<SettingValue> => request(`/settings/${encodeURIComponent(key)}`);

export const updateSetting = (key: string, value: string, updatedBy: string): Promise<SettingValue> =>
  request(`/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value, updated_by: updatedBy }),
  });

// -- Approval queue -------------------------------------------------------

export const listApprovals = (): Promise<ApprovalRequest[]> => request("/approvals");

export const approveApproval = (id: string, decidedBy: string): Promise<ToolExecutionResult> =>
  request(`/approvals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ decided_by: decidedBy }),
  });

export const rejectApproval = (id: string, decidedBy: string, reason?: string): Promise<ApprovalRequest> =>
  request(`/approvals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ decided_by: decidedBy, reason: reason ?? null }),
  });

// -- Learning proposal queue ------------------------------------------------

export const listLearningProposals = (): Promise<LearningProposal[]> => request("/learning/proposals");

export const approveLearningProposal = (id: string, decidedBy: string): Promise<LearningProposal> =>
  request(`/learning/proposals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ decided_by: decidedBy }),
  });

export const rejectLearningProposal = (id: string, decidedBy: string, reason?: string): Promise<LearningProposal> =>
  request(`/learning/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ decided_by: decidedBy, reason: reason ?? null }),
  });

export const triggerReflection = (days = 7): Promise<ReflectionResult> =>
  request("/learning/reflect", { method: "POST", body: JSON.stringify({ days }) });

// -- Cost -------------------------------------------------------------------

export const getCostSummary = (): Promise<CostSummary> => request("/cost");
