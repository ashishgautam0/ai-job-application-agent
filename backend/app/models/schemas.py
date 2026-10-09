from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional
from datetime import date


# ---- Profile ----

class ProjectEntry(BaseModel):
    name: str = ""
    description: str = ""
    keywords: list[str] = []


class ExperienceEntry(BaseModel):
    role: str = ""
    company: str = ""
    period: str = ""
    description: str = ""


class UserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: Optional[str] = None
    bio: Optional[str] = None
    skills: Optional[list[str]] = None
    projects: Optional[list[ProjectEntry]] = None
    experience: Optional[list[ExperienceEntry]] = None
    education: Optional[str] = None
    location_preference: Optional[str] = None
    target_roles: Optional[list[str]] = None
    scoring_weights: Optional[dict] = None


class UserProfileResponse(BaseModel):
    id: Optional[int] = None
    username: str = "subidh"
    full_name: str = ""
    bio: str = ""
    skills: list[str] = []
    projects: list[ProjectEntry] = []
    experience: list[ExperienceEntry] = []
    education: str = ""
    location_preference: str = ""
    target_roles: list[str] = []
    resume_text: str = ""
    scoring_weights: dict = {}
    updated_at: Optional[str] = None


class ApplicationPromptSettings(BaseModel):
    """The prompt templates stored with the backend profile."""
    model_config = ConfigDict(extra="forbid")
    prompt_template: str = Field(default="", max_length=12_000)
    automation_rules: str = Field(default="", max_length=12_000)
    desktop_prompt_template: str = Field(default="", max_length=40_000)
    followup_template: str = Field(default="", max_length=12_000)
    cold_dm_template: str = Field(default="", max_length=12_000)


class CompanyExclusionsSettings(BaseModel):
    """User-managed employer names omitted from future scraped job intake."""
    model_config = ConfigDict(extra="forbid")
    companies: list[str] = Field(default_factory=list, max_length=100)


class CompanyExclusionsResponse(BaseModel):
    """The user's own exclusions — the only employers the agent skips."""
    companies: list[str]


class RenderedApplicationPrompt(BaseModel):
    """A fixed, ready-to-paste browser-agent batch assembled by the backend."""
    prompt: str
    job_count: int
    resume_available: bool
    ready: bool
    issues: list[str] = Field(default_factory=list)
    unresolved_placeholders: list[str] = Field(default_factory=list)


class ReviewedExperienceEntry(BaseModel):
    id: str = ""
    label: str = ""
    role: str = ""
    company: str = ""
    start: str = ""
    end: str = ""


class ReviewedEducationEntry(BaseModel):
    id: str = ""
    level: str
    credential: str
    field: str = ""
    institution: str = ""


class ResumeProfileReviewRequest(BaseModel):
    skills: list[str] = Field(default_factory=list)
    experience: list[ReviewedExperienceEntry] = Field(default_factory=list)
    education: list[ReviewedEducationEntry] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    review_notes: str = ""


class ResumeProfileResponse(BaseModel):
    backend_text: str = ""
    id: int
    username: str
    version: int
    source_kind: str
    source_filename: str
    source_sha256: str
    extraction_method: str
    facts: dict = Field(default_factory=dict)
    extracted_facts: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    readability: dict = Field(default_factory=dict)
    status: str
    created_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    activated_at: Optional[str] = None


class ResumeProfileStatusResponse(BaseModel):
    active: Optional[ResumeProfileResponse] = None
    latest: Optional[ResumeProfileResponse] = None


# ---- Applications ----

class AddApplicationRequest(BaseModel):
    company: str
    role: str
    job_type: str = "Job"
    platform: str = ""
    url: str = ""
    noc_compatible: str = "Unknown"
    conversion: str = "N/A"
    salary: str = ""
    notes: str = ""


class DesktopAgentJobRequest(BaseModel):
    """One posting the Claude Desktop agent handled during a run."""
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2_000)
    source: str = Field(min_length=1, max_length=60)
    location: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20_000)
    status: Literal["applied", "skipped"] = "applied"
    job_type: str = Field(default="Job", max_length=60)
    notes: str = Field(default="", max_length=2_000)


class DesktopAgentColdDmRequest(BaseModel):
    """One LinkedIn connection invitation with a note the desktop agent sent."""
    model_config = ConfigDict(extra="forbid")
    tracker_id: int = Field(gt=0)
    recipient_name: str = Field(min_length=1, max_length=200)
    recipient_profile_url: str = Field(
        min_length=1, max_length=500, pattern=r"^https://([a-z]+\.)?linkedin\.com/")
    note: str = Field(min_length=1, max_length=1_000)


class UpdateStatusRequest(BaseModel):
    status: str


class UpdateNotesRequest(BaseModel):
    notes: str


class SnoozeRequest(BaseModel):
    new_date: date


class MarkScrapedJobRequest(BaseModel):
    action: str  # "applied" or "dismissed"


# ---- Company Research ----

class CompanyResearchRequest(BaseModel):
    company_name: str


# ---- Follow-up History ----

class LogFollowUpRequest(BaseModel):
    entity_type: str   # "application"
    entity_id: int
    message_content: str = ""
    channel: str = ""


class UpdateFollowUpOutcomeRequest(BaseModel):
    outcome: str       # "pending", "responded", "no_response"


# ---- Mini Demos ----

class AddDemoRequest(BaseModel):
    company: str
    role: str
    demo_idea: str


class UpdateDemoRequest(BaseModel):
    status: Optional[str] = None
    github_url: Optional[str] = None
    demo_url: Optional[str] = None
    hours_spent: Optional[float] = None
    result: Optional[str] = None


# ---- Push Subscriptions ----

class PushSubscriptionRequest(BaseModel):
    endpoint: str
    keys: dict

