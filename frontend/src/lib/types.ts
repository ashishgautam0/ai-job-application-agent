// ---- Applications ----
export interface Application {
  id: number;
  company: string;
  role: string;
  type: string;
  platform: string;
  url: string;
  noc_compatible: string;
  conversion: string;
  salary: string;
  notes: string;
  status: string;
  date_applied?: string;
  follow_up_date?: string;
  follow_up_count?: number;
  scraped_job_id?: number | null;
}

export interface AddApplicationRequest {
  company: string;
  role: string;
  job_type?: string;
  platform?: string;
  url?: string;
  noc_compatible?: string;
  conversion?: string;
  salary?: string;
  notes?: string;
}

// ---- Dashboard / Stats ----
export interface DashboardStats {
  total: number;
  applied: number;
  interview: number;
  offer: number;
  rejected: number;
  this_week: number;
  today: number;
  daily_target: number;
  dms_today: number;
  dm_target: number;
  [key: string]: number;
}

export interface FollowUp {
  id: number;
  company: string;
  role: string;
  follow_up_date: string;
  status: string;
  platform?: string;
  follow_up_count?: number;
  scraped_job_id?: number | null;
  // A follow-up is only sendable with both halves: a written draft and an
  // address to send it to.
  draft_ready?: boolean;
  recipient?: string | null;
}

export interface ColdDmTodo {
  id: number;
  company: string;
  role: string;
  follow_up_date: string;
  scraped_job_id: number | null;
  cold_dm_ready: boolean;
  readiness_issue?: string | null;
}

export interface FollowUpDraft {
  entity_id: number;
  status: "pending" | "ready" | null;
  content: string | null;
  request_id?: number;
  follow_up_number?: number;
}

export interface FollowUpHistory {
  id: number;
  entity_type: "application";
  entity_id: number;
  message_content: string;
  channel: string;
  follow_up_number: number;
  follow_up_outcome: "pending" | "responded" | "no_response";
  sent_at: string;
}

export interface FollowUpEffectiveness {
  by_channel: { channel: string; total: number; responded: number; rate: number }[];
  by_number: { follow_up_number: number; total: number; responded: number; rate: number }[];
  overall: { total: number; responded: number; rate: number };
}

export interface LogFollowUpRequest {
  entity_type: string;
  entity_id: number;
  message_content?: string;
  channel?: string;
}

export interface WeeklyTrend {
  week: string;
  week_end?: string;
  Job?: number;
  Internship?: number;
  total: number;
  [key: string]: string | number | undefined;
}

export interface PlatformEffectiveness {
  platform: string;
  applications: number;
  responses: number;
  response_rate: number;
}

export interface StatusFunnel {
  [status: string]: number;
}

export interface RoleAnalysis {
  role_keyword: string;
  applied: number;
  responses: number;
  response_rate: number;
  example_roles?: string[];
}

// ---- Scraped Jobs ----
export interface ScrapedJob {
  screening_status?: "pending" | "pass" | "fail" | "review";
  screening_reason?: string;
  id?: number;
  title: string;
  company: string;
  location: string;
  source: string;
  url: string;
  description: string;
  score: number;
  work_mode?: string;
  llm_reason?: string;
  verdict?: string;
  ats_score?: number | null;
  skill_match?: number | null;
  noc_verdict?: string;
  applied?: number;
  bestscore?: number | null;
  bestscore_breakdown?: {
    match?: number | null;
    eligibility?: string;
    reason?: string;
    fit?: number;
    fit_source?: string;
    freshness: number;
    ease: number;
    aws?: number;
    score: number;
  };
}

export interface JobMessage {
  job_id: number;
  message_type: string;
  content: string | null;
  generated_by?: string;
  generated_at?: string;
  is_outdated?: boolean;
  resume_version?: number;
  jd_version?: number;
  jd_hash?: string;
  match_score?: number;
  analysis_version?: string;
  generation_rules_version?: string;
  run_id?: string;
}

// ---- Company Research ----
export interface CompanyIntel {
  product_url?: string;
  [key: string]: unknown;
}

export interface CachedCompanyIntel {
  found: boolean;
  company_name?: string;
  product_url?: string;
  researched_at?: string;
}

export type EmailStatus =
  | "valid"
  | "catch_all"
  | "invalid"
  | "pattern"
  | "no_mx";

export interface EmailCandidate {
  email: string;
  status: EmailStatus;
}

export interface RecruiterContact {
  name: string;
  candidates: EmailCandidate[];
}

export interface RecruiterEmailReport {
  ok: boolean;
  reason?: string;
  message?: string;
  domain?: string;
  mx_ok?: boolean;
  smtp_checked?: boolean;
  catch_all?: boolean | null;
  email_pattern?: string | null;
  contacts?: RecruiterContact[];
}

// ---- Profile ----
export interface ProjectEntry {
  name: string;
  description: string;
  keywords: string[];
}

export interface ExperienceEntry {
  role: string;
  company: string;
  period: string;
  description: string;
}

export interface UserProfile {
  id?: number;
  username: string;
  full_name: string;
  bio: string;
  skills: string[];
  projects: ProjectEntry[];
  experience: ExperienceEntry[];
  education: string;
  location_preference: string;
  target_roles: string[];
  resume_text: string;
  scoring_weights: Record<string, unknown>;
  updated_at?: string;
}

export interface UserProfileUpdate {
  full_name?: string;
  bio?: string;
  skills?: string[];
  projects?: ProjectEntry[];
  experience?: ExperienceEntry[];
  education?: string;
  location_preference?: string;
  target_roles?: string[];
  scoring_weights?: Record<string, unknown>;
}

export interface EvidenceExcerpt {
  source?: string;
  line?: number | null;
  excerpt?: string;
}

export interface ResumeFact {
  id?: string;
  name?: string;
  label?: string;
  role?: string;
  company?: string;
  start?: string;
  end?: string;
  level?: string;
  credential?: string;
  field?: string;
  institution?: string;
  evidence?: EvidenceExcerpt[];
}

export interface ResumeProfile {
  backend_text?: string;
  id: number;
  username: string;
  version: number;
  source_kind: "pdf";
  source_filename: string;
  source_sha256: string;
  extraction_method: string;
  facts: {
    skills?: ResumeFact[];
    experience?: ResumeFact[];
    education?: ResumeFact[];
    certifications?: ResumeFact[];
    total_experience_months?: number | null;
  };
  extracted_facts: Record<string, unknown>;
  evidence: Record<string, unknown>;
  readability: { status?: string; warnings?: string[]; [key: string]: unknown };
  status: "pending_review" | "active" | "superseded";
  created_at?: string;
  reviewed_at?: string;
  activated_at?: string;
}

export interface ResumeProfileStatus {
  active: ResumeProfile | null;
  latest: ResumeProfile | null;
}

export interface ApplicationResumeStatus {
  available: boolean;
  filename?: string;
  sha256?: string;
  size?: number;
  version?: number;
  profile_status?: ResumeProfile["status"];
}

export interface ApplicationPromptSettings {
  followup_template: string;
  cold_dm_template: string;
  prompt_template: string;
  automation_rules: string;
  desktop_prompt_template: string;
}

export interface DesktopPromptResponse {
  content: string;
  template: string;
  customized: boolean;
  issues: string[];
  unresolved_placeholders: string[];
}

export interface RenderedApplicationPrompt {
  prompt: string;
  job_count: number;
  resume_available: boolean;
  ready: boolean;
  issues: string[];
  unresolved_placeholders: string[];
}

export interface ResumeProfileReview {
  skills: string[];
  experience: Array<{ id?: string; label?: string; role?: string; company?: string; start?: string; end?: string }>;
  education: Array<{ id?: string; level: string; credential: string; field?: string; institution?: string }>;
  certifications: string[];
  review_notes?: string;
}

// ---- Notifications ----
export interface AppNotification {
  id: number;
  title: string;
  body: string;
  type: string;
  metadata: Record<string, unknown>;
  is_read: boolean;
  created_at: string;
}

export interface UnreadCountResponse {
  count: number;
}
