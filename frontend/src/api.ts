import type {
  AgentActivity,
  ApiErrorBody,
  ApprovalRequest,
  BrandVoice,
  CostSummary,
  LearningProposal,
  PlatformCredentialStatus,
  ReflectionResult,
  SettingValue,
  ToolExecutionResult,
  WorkflowResult,
  DashboardSession,
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8010";
let csrfToken: string | null = null;

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method) ? { "X-CSRF-Token": csrfToken } : {}),
      ...init?.headers,
    },
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

// -- Authentication -----------------------------------------------------

export const login = async (username: string, password: string): Promise<DashboardSession> => {
  const session = await request<DashboardSession>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  csrfToken = session.csrf_token;
  return session;
};

export const getCurrentSession = async (): Promise<DashboardSession> => {
  const session = await request<DashboardSession>("/auth/me");
  csrfToken = session.csrf_token;
  return session;
};

export const logout = async (): Promise<void> => {
  try {
    await request("/auth/logout", { method: "POST" });
  } finally {
    csrfToken = null;
  }
};

// -- Health -------------------------------------------------------------

export const getHealth = (): Promise<{ status: string }> => request("/health");

// -- Settings -------------------------------------------------------------

export const getSetting = (key: string): Promise<SettingValue> => request(`/settings/${encodeURIComponent(key)}`);

export const updateSetting = (key: string, value: string): Promise<SettingValue> =>
  request(`/settings/${encodeURIComponent(key)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });

// -- Approval queue -------------------------------------------------------

export const listApprovals = (): Promise<ApprovalRequest[]> => request("/approvals");

export const retryApproval = (id: string): Promise<ToolExecutionResult> =>
  request("/approvals/" + encodeURIComponent(id) + "/retry", {
    method: "POST",
    body: JSON.stringify({}),
  });

export const approveApproval = (id: string): Promise<ToolExecutionResult> =>
  request(`/approvals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({}),
  });

export const rejectApproval = (id: string, reason?: string): Promise<ApprovalRequest> =>
  request(`/approvals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });

// -- Learning proposal queue ------------------------------------------------

export const listLearningProposals = (): Promise<LearningProposal[]> => request("/learning/proposals");

export const approveLearningProposal = (id: string): Promise<LearningProposal> =>
  request(`/learning/proposals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({}),
  });

export const rejectLearningProposal = (id: string, reason?: string): Promise<LearningProposal> =>
  request(`/learning/proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });

export const triggerReflection = (days = 7): Promise<ReflectionResult> =>
  request("/learning/reflect", { method: "POST", body: JSON.stringify({ days }) });

// -- Cost -------------------------------------------------------------------

export const getCostSummary = (): Promise<CostSummary> => request("/cost");

// -- Live activity ------------------------------------------------------

export const getActivity = (): Promise<AgentActivity | null> => request("/activity");

// -- Brand voice --------------------------------------------------------

export const listBrandVoices = (): Promise<BrandVoice[]> => request("/brand-voice");

export const createBrandVoice = (title: string, content: string): Promise<BrandVoice> =>
  request("/brand-voice", { method: "POST", body: JSON.stringify({ title, content }) });

export const updateBrandVoice = (id: string, title: string, content: string): Promise<BrandVoice> =>
  request(`/brand-voice/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({ title, content }),
  });

export const deleteBrandVoice = (id: string): Promise<{ deleted: boolean }> =>
  request(`/brand-voice/${encodeURIComponent(id)}`, { method: "DELETE" });

// -- Manual workflow triggers ---------------------------------------------

export const triggerResearchWorkflow = (query: string, sources: string[] | null, limitPerSource: number): Promise<WorkflowResult> =>
  request("/workflows/research", {
    method: "POST",
    body: JSON.stringify({ query, sources, limit_per_source: limitPerSource }),
  });

export const triggerContentWorkflow = (calendarEntries: string[], recentPostTopics: string[]): Promise<WorkflowResult> =>
  request("/workflows/content", {
    method: "POST",
    body: JSON.stringify({ calendar_entries: calendarEntries, recent_post_topics: recentPostTopics }),
  });

export const triggerAnalyticsWorkflow = (periodStart?: string, periodEnd?: string): Promise<WorkflowResult> =>
  request("/workflows/analytics", {
    method: "POST",
    body: JSON.stringify({ period_start: periodStart ?? null, period_end: periodEnd ?? null }),
  });

export const triggerEngagementWorkflow = (
  notificationType: "comment" | "dm" | "connection_request",
  text: string,
): Promise<WorkflowResult> =>
  request("/workflows/engagement", {
    method: "POST",
    body: JSON.stringify({ notification_type: notificationType, text }),
  });

// -- Connections (platform credentials) ------------------------------------

export const listCredentials = (): Promise<PlatformCredentialStatus[]> => request("/credentials");

export const saveCredentials = (platformId: string, values: Record<string, string>): Promise<PlatformCredentialStatus> =>
  request(`/credentials/${encodeURIComponent(platformId)}`, {
    method: "PUT",
    body: JSON.stringify({ values }),
  });

export const deleteCredentials = (platformId: string): Promise<{ deleted: boolean }> =>
  request(`/credentials/${encodeURIComponent(platformId)}`, { method: "DELETE" });
