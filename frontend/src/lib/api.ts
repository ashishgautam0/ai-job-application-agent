import type {
  AddApplicationRequest,
  Application,
  CachedCompanyIntel,
  DashboardStats,
  ColdDmTodo,
  FollowUp,
  HrEmailTodo,
  FollowUpDraft,
  FollowUpEffectiveness,
  FollowUpHistory,
  JobMessage,
  LogFollowUpRequest,
  PlatformEffectiveness,
  RoleAnalysis,
  ScrapedJob,
  StatusFunnel,
  UserProfile,
  UserProfileUpdate,
  WeeklyTrend,
  AppNotification,
  UnreadCountResponse,
  ResumeProfile,
  ResumeProfileReview,
  ResumeProfileStatus,
  ApplicationResumeStatus,
  ApplicationPromptSettings,
  RenderedApplicationPrompt,
  DesktopPromptResponse,
} from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) || {}),
  };

  const res = await fetch(`${API_URL}${path}`, { ...options, headers });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(body || `Request failed: ${res.status}`);
  }

  return res.json();
}

// ---- Applications ----
export async function getApplications(filters?: {
  status?: string;
  type?: string;
  platform?: string;
}): Promise<Application[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.type) params.set("type", filters.type);
  if (filters?.platform) params.set("platform", filters.platform);
  const qs = params.toString();
  return apiFetch<Application[]>(`/api/applications${qs ? `?${qs}` : ""}`);
}

export async function lookupApplication(url: string): Promise<Application | null> {
  return apiFetch<Application | null>(`/api/applications/lookup?url=${encodeURIComponent(url)}`);
}

export async function createApplication(data: AddApplicationRequest) {
  return apiFetch<{ success: boolean }>("/api/applications", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateApplicationStatus(id: number, status: string) {
  return apiFetch<{ success: boolean }>(`/api/applications/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export async function updateApplicationNotes(id: number, notes: string) {
  return apiFetch<{ success: boolean }>(`/api/applications/${id}/notes`, {
    method: "PATCH",
    body: JSON.stringify({ notes }),
  });
}

export async function deleteApplication(id: number) {
  return apiFetch<{ success: boolean }>(`/api/applications/${id}`, {
    method: "DELETE",
  });
}

export async function snoozeFollowUp(id: number, newDate: string) {
  return apiFetch<{ success: boolean }>(`/api/applications/${id}/snooze`, {
    method: "PATCH",
    body: JSON.stringify({ new_date: newDate }),
  });
}

// ---- Stats ----
export async function getDashboard(): Promise<DashboardStats> {
  return apiFetch<DashboardStats>("/api/stats/dashboard");
}

export async function getFollowUps(): Promise<FollowUp[]> {
  return apiFetch<FollowUp[]>("/api/stats/follow-ups");
}

export async function getColdDmTodos(): Promise<ColdDmTodo[]> {
  return apiFetch<ColdDmTodo[]>("/api/stats/cold-dm-todos");
}

export async function getHrEmailTodos(): Promise<HrEmailTodo[]> {
  return apiFetch<HrEmailTodo[]>("/api/stats/hr-email-todos");
}

export async function setHrEmailTodoCompleted(id: number, completed = true) {
  return apiFetch<{ success: boolean; hr_email_sent_at: string | null }>(
    `/api/applications/${id}/hr-email-todo`,
    { method: "PATCH", body: JSON.stringify({ completed }) },
  );
}

export async function getWeeklyTrend(): Promise<WeeklyTrend[]> {
  return apiFetch<WeeklyTrend[]>("/api/stats/weekly-trend");
}

export async function getPlatformEffectiveness(): Promise<PlatformEffectiveness[]> {
  return apiFetch<PlatformEffectiveness[]>("/api/stats/platform-effectiveness");
}

export async function getStatusFunnel(): Promise<StatusFunnel> {
  return apiFetch<StatusFunnel>("/api/stats/status-funnel");
}

export async function getRoleAnalysis(): Promise<RoleAnalysis[]> {
  return apiFetch<RoleAnalysis[]>("/api/stats/role-analysis");
}

// ---- Scraper ----
export async function getScrapedJobs(): Promise<ScrapedJob[]> {
  return apiFetch<ScrapedJob[]>("/api/scraped-jobs");
}

export async function markScrapedJob(id: number, action: string) {
  return apiFetch<{ success: boolean }>(`/api/scraped-jobs/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ action }),
  });
}

export async function getCachedCompanyIntel(
  name: string
): Promise<CachedCompanyIntel> {
  return apiFetch<CachedCompanyIntel>(
    `/api/company-research/cached?name=${encodeURIComponent(name)}`
  );
}

export async function findRecruiterEmails(
  company: string,
  names = "",
  domain = ""
): Promise<import("./types").RecruiterEmailReport> {
  const q = new URLSearchParams({ company });
  if (names) q.set("names", names);
  if (domain) q.set("domain", domain);
  return apiFetch(`/api/company-research/recruiter-emails?${q.toString()}`);
}

export async function getScrapedJob(id: number): Promise<ScrapedJob> {
  return apiFetch<ScrapedJob>(`/api/scraped-jobs/${id}`);
}

export async function lookupScrapedJob(url: string): Promise<{ id: number | null }> {
  return apiFetch<{ id: number | null }>(
    `/api/scraped-jobs/lookup?url=${encodeURIComponent(url)}`
  );
}

export async function getFollowUpDraft(entityId: number): Promise<FollowUpDraft> {
  return apiFetch<FollowUpDraft>(`/api/follow-ups/draft?entity_id=${entityId}`);
}

export async function getJobMessage(
  id: number,
  type: string = "cold_dm"
): Promise<JobMessage> {
  return apiFetch<JobMessage>(
    `/api/scraped-jobs/${id}/message?type=${encodeURIComponent(type)}`
  );
}

// ---- Follow-up History ----
export async function logFollowUp(data: LogFollowUpRequest) {
  return apiFetch<{ success: boolean; follow_up_number: number }>("/api/follow-ups/log", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function getFollowUpHistory(
  entityType: string,
  entityId: number,
): Promise<FollowUpHistory[]> {
  return apiFetch<FollowUpHistory[]>(
    `/api/follow-ups/history?entity_type=${entityType}&entity_id=${entityId}`,
  );
}

export async function updateFollowUpOutcome(historyId: number, outcome: string) {
  return apiFetch<{ success: boolean }>(`/api/follow-ups/${historyId}/outcome`, {
    method: "PATCH",
    body: JSON.stringify({ outcome }),
  });
}

export async function getFollowUpEffectiveness(): Promise<FollowUpEffectiveness> {
  return apiFetch<FollowUpEffectiveness>("/api/follow-ups/effectiveness");
}

// ---- Profile ----
export async function getProfile(): Promise<UserProfile> {
  return apiFetch<UserProfile>("/api/profile/");
}

export async function updateProfile(data: UserProfileUpdate): Promise<UserProfile> {
  return apiFetch<UserProfile>("/api/profile/", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function getResumeProfileStatus(): Promise<ResumeProfileStatus> {
  return apiFetch<ResumeProfileStatus>("/api/profile/resume");
}

export async function getApplicationResumeStatus(): Promise<ApplicationResumeStatus> {
  return apiFetch<ApplicationResumeStatus>("/api/profile/resume/application");
}

export async function getApplicationPromptSettings(): Promise<ApplicationPromptSettings> {
  return apiFetch<ApplicationPromptSettings>("/api/profile/application-settings");
}

// The user's own exclusion list — the only employers the agent skips.
export interface CompanyExclusions { companies: string[] }

export async function getCompanyExclusions(): Promise<CompanyExclusions> {
  return apiFetch<CompanyExclusions>("/api/profile/company-exclusions");
}

export async function updateCompanyExclusions(companies: string[]): Promise<CompanyExclusions> {
  return apiFetch<CompanyExclusions>("/api/profile/company-exclusions", {
    method: "PUT",
    body: JSON.stringify({ companies }),
  });
}

export async function updateApplicationPromptSettings(
  data: Partial<ApplicationPromptSettings>,
): Promise<ApplicationPromptSettings> {
  return apiFetch<ApplicationPromptSettings>("/api/profile/application-settings", {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function getRenderedApplicationPrompt(
  pageUrl: string,
): Promise<RenderedApplicationPrompt> {
  return apiFetch<RenderedApplicationPrompt>(
    `/api/profile/application-prompt?page_url=${encodeURIComponent(pageUrl)}`,
  );
}

export function getApplicationResumePdfUrl(): string {
  return `${API_URL}/api/profile/resume/pdf`;
}

export async function getRenderedOutreachPrompt(pageUrl: string, kind: "hr_email" | "followup" | "cold_dm"): Promise<RenderedApplicationPrompt> {
  return apiFetch<RenderedApplicationPrompt>(
    `/api/profile/outreach-prompt?page_url=${encodeURIComponent(pageUrl)}&kind=${kind}`,
  );
}

export async function getDesktopPrompt(): Promise<DesktopPromptResponse> {
  return apiFetch<DesktopPromptResponse>("/api/profile/desktop-prompt");
}

export async function uploadResumePdf(file: File): Promise<ResumeProfile> {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_URL}/api/profile/resume`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    let message = `Upload failed: ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      const body = await res.text();
      if (body) message = body;
    }
    throw new Error(message);
  }

  return res.json();
}

export async function activateResumeProfile(
  profileId: number,
  review: ResumeProfileReview,
): Promise<ResumeProfile> {
  return apiFetch<ResumeProfile>(`/api/profile/resume/${profileId}/activate`, {
    method: "PUT",
    body: JSON.stringify(review),
  });
}

// ---- Notifications ----
export async function getNotifications(unreadOnly = false): Promise<AppNotification[]> {
  const qs = unreadOnly ? "?unread_only=true" : "";
  return apiFetch<AppNotification[]>(`/api/notifications${qs}`);
}

export async function getUnreadCount(): Promise<UnreadCountResponse> {
  return apiFetch<UnreadCountResponse>("/api/notifications/unread-count");
}

export async function markNotificationRead(id: number) {
  return apiFetch<{ success: boolean }>(`/api/notifications/${id}/read`, {
    method: "PATCH",
  });
}

export async function markAllNotificationsRead() {
  return apiFetch<{ success: boolean }>("/api/notifications/mark-all-read", {
    method: "POST",
  });
}

export async function getVapidPublicKey(): Promise<{ public_key: string }> {
  return apiFetch<{ public_key: string }>("/api/vapid-public-key");
}

export async function subscribePush(subscription: PushSubscriptionJSON) {
  return apiFetch<{ success: boolean }>("/api/notifications/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      keys: subscription.keys,
    }),
  });
}

export async function unsubscribePush(subscription: PushSubscriptionJSON) {
  return apiFetch<{ success: boolean }>("/api/notifications/push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: subscription.endpoint,
      keys: subscription.keys,
    }),
  });
}
