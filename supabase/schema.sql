-- =============================================================================
-- Job Search HQ — Supabase (PostgreSQL) schema
--
-- Apply once to a new Supabase project:
--   Dashboard -> SQL Editor -> New query -> paste this file -> Run
--
-- Every table, column, default and index below is derived from the code that
-- reads or writes it (backend/modules/tracker.py, profile.py, hourly.py and
-- the routers under backend/app/routers/). Column names must match exactly —
-- the backend talks to PostgREST, which fails on unknown columns.
--
-- ACCESS MODEL
-- The Next.js frontend never talks to Supabase directly; it calls the FastAPI
-- backend, which holds the only Supabase credential. So RLS is enabled on every
-- table with NO policies: anon and authenticated get nothing, and only the
-- service_role key (which bypasses RLS) can read or write. Set SUPABASE_KEY to
-- the service_role key on the backend, and never expose it to the browser.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- applications — manually tracked job applications
-- ---------------------------------------------------------------------------
create table if not exists applications (
    id                    bigserial primary key,
    company               text        not null,
    role                  text        not null,
    type                  text        not null default 'Job',      -- 'Job' | 'Internship'
    platform              text        not null default '',
    url                   text        not null default '',
    date_applied          date        not null default current_date,
    follow_up_date        date,                                    -- null once terminal
    follow_up_count       integer     not null default 0,
    status                text        not null default 'Applied',
    noc_compatible        text        not null default 'Unknown',
    conversion_potential  text        not null default 'N/A',
    salary_range          text        not null default '',
    notes                 text        not null default '',
    created_at            timestamptz not null default now()
);

create index if not exists idx_applications_status         on applications (status);
create index if not exists idx_applications_follow_up_date on applications (follow_up_date);
create index if not exists idx_applications_date_applied   on applications (date_applied desc);


-- ---------------------------------------------------------------------------
-- scraped_jobs — LinkedIn scraper output
-- url is UNIQUE because save_scraped_job() upserts on_conflict="url".
-- ---------------------------------------------------------------------------
create table if not exists scraped_jobs (
    id           bigserial primary key,
    title        text        not null,
    company      text        not null default 'Unknown',
    location     text        not null default '',
    source       text        not null default '',
    url          text        not null unique,
    description  text        not null default '',
    score        integer     not null default 0,
    verdict      text        not null default '',
    ats_score    integer,
    skill_match  integer,
    noc_verdict  text        not null default '',
    profile_version integer,
    analysis_version text,
    analysis_stale boolean not null default true,
    analysis_details jsonb not null default '{}'::jsonb,
    analyzed_at timestamptz,
    jd_version integer not null default 1,
    jd_hash text,
    work_mode    text,   -- rendered by the Tonight page; no writer yet
    llm_reason   text,   -- rendered by the Tonight page; no writer yet
    dismissed    smallint    not null default 0,   -- 0/1, not boolean: code filters .eq("dismissed", 0)
    applied      smallint    not null default 0,   -- 0/1, set by mark_scraped_job()
    scraped_at   timestamptz not null default now()
);

create index if not exists idx_scraped_jobs_inbox      on scraped_jobs (dismissed, applied);
create index if not exists idx_scraped_jobs_scraped_at on scraped_jobs (scraped_at desc);
create index if not exists idx_scraped_jobs_source     on scraped_jobs (source);

-- Upgrade existing deployments created before profile-scoped analysis.
alter table scraped_jobs add column if not exists profile_version integer;
alter table scraped_jobs add column if not exists analysis_version text;
alter table scraped_jobs add column if not exists analysis_stale boolean not null default true;
alter table scraped_jobs add column if not exists analysis_details jsonb not null default '{}'::jsonb;
alter table scraped_jobs add column if not exists analyzed_at timestamptz;
alter table scraped_jobs add column if not exists jd_version integer not null default 1;
alter table scraped_jobs add column if not exists jd_hash text;
alter table scraped_jobs alter column ats_score drop not null;
alter table scraped_jobs alter column ats_score drop default;
alter table scraped_jobs alter column skill_match drop not null;
alter table scraped_jobs alter column skill_match drop default;
create index if not exists idx_scraped_jobs_analysis on scraped_jobs (analysis_stale, profile_version);


-- ---------------------------------------------------------------------------
-- follow_up_history — one row per follow-up message sent
-- entity_type is 'application'; entity_id points at that table.
-- Deliberately not a foreign key.
-- ---------------------------------------------------------------------------
create table if not exists follow_up_history (
    id                 bigserial primary key,
    entity_type        text        not null,
    entity_id          bigint      not null,
    message_content    text        not null default '',
    channel            text        not null default '',
    follow_up_number   integer     not null default 1,
    follow_up_outcome  text        not null default 'pending',  -- pending | responded | no_response
    sent_at            timestamptz not null default now(),
    created_at         timestamptz not null default now(),
    -- Who the Cold DM went to. The desktop agent also leaves a human-readable
    -- "Sent to <name>: <url>" trailer on message_content; these columns are the
    -- queryable copy, and what _connection_recipient prefers.
    recipient_name         text,
    recipient_profile_url  text,
    -- Set when the invitation is confirmed accepted. Not yet written by anything.
    accepted_at            timestamptz
);

create index if not exists idx_follow_up_history_entity  on follow_up_history (entity_type, entity_id);
create index if not exists idx_follow_up_history_outcome on follow_up_history (follow_up_outcome);


-- ---------------------------------------------------------------------------
-- company_research_cache — 14-day cache of websites and hiring contacts
-- ---------------------------------------------------------------------------
create table if not exists company_research_cache (
    id                       bigserial primary key,
    company_name             text        not null unique,
    hiring_contact_name      text        not null default '',
    hiring_contact_title     text        not null default '',
    hiring_contact_linkedin  text        not null default '',
    product_url              text        not null default '',
    researched_at            timestamptz not null default now()
);

-- save_research_cache() never sets researched_at, so an upsert that UPDATEs an
-- existing row would leave the original timestamp in place. Once that row aged
-- past the 14-day staleness check in get_cached_research(), it would be treated
-- as stale forever and the cache would stop working. This trigger re-stamps it.
create or replace function touch_researched_at()
returns trigger
language plpgsql
as $$
begin
    new.researched_at = now();
    return new;
end;
$$;

drop trigger if exists trg_company_research_cache_touch on company_research_cache;
create trigger trg_company_research_cache_touch
    before update on company_research_cache
    for each row execute function touch_researched_at();


-- ---------------------------------------------------------------------------
-- mini_demos — small build-for-a-company projects
-- ---------------------------------------------------------------------------
create table if not exists mini_demos (
    id           bigserial primary key,
    company      text        not null,
    role         text        not null default '',
    demo_idea    text        not null default '',
    status       text        not null default 'Idea',   -- Idea | Building | Deployed | ...
    github_url   text        not null default '',
    demo_url     text        not null default '',
    hours_spent  numeric     not null default 0,
    result       text        not null default '',
    created_at   timestamptz not null default now()
);

create index if not exists idx_mini_demos_status     on mini_demos (status);
create index if not exists idx_mini_demos_created_at on mini_demos (created_at desc);


-- ---------------------------------------------------------------------------
-- notifications — in-app notification feed
-- Mirrors the CREATE TABLE that init_notifications_table() tries to run via an
-- exec_sql RPC. That RPC does not exist in a stock Supabase project, so the
-- call fails harmlessly and prints a note; applying this file is what actually
-- creates the table. Do NOT add an exec_sql RPC — it would expose arbitrary SQL.
-- ---------------------------------------------------------------------------
create table if not exists notifications (
    id          bigserial primary key,
    title       text        not null,
    body        text        not null,
    type        text        not null default 'job_alert',
    metadata    jsonb       not null default '{}'::jsonb,
    is_read     boolean     not null default false,
    created_at  timestamptz not null default now()
);

create index if not exists idx_notifications_is_read    on notifications (is_read);
create index if not exists idx_notifications_created_at on notifications (created_at desc);


-- ---------------------------------------------------------------------------
-- push_subscriptions — Web Push endpoints, upserted on_conflict="endpoint"
-- ---------------------------------------------------------------------------
create table if not exists push_subscriptions (
    id          bigserial primary key,
    endpoint    text        not null unique,
    keys_p256dh text        not null,
    keys_auth   text        not null,
    created_at  timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- user_profile — single-user profile, upserted on_conflict="username"
-- The jsonb columns are written as real lists/dicts by upsert_profile(), so
-- they round-trip correctly and are queryable.
-- ---------------------------------------------------------------------------
create table if not exists user_profile (
    id                   bigserial primary key,
    username             text        not null unique,
    full_name            text        not null default '',
    bio                  text        not null default '',
    skills               jsonb       not null default '[]'::jsonb,
    projects             jsonb       not null default '[]'::jsonb,
    experience           jsonb       not null default '[]'::jsonb,
    education            text        not null default '',
    location_preference  text        not null default '',
    target_roles         jsonb       not null default '[]'::jsonb,
    resume_text          text        not null default '',
    blocked_companies    jsonb       not null default '[]'::jsonb,
    scoring_weights      jsonb       not null default '{}'::jsonb,
    updated_at           timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- resume_profiles — immutable PDF extraction + separately reviewed facts.
-- Exactly one reviewed version may be active for a username. Raw PDF bytes are
-- not stored; the SHA-256 identifies the source without exposing the document.
-- ---------------------------------------------------------------------------
create table if not exists resume_profiles (
    id                  bigserial primary key,
    username            text        not null,
    version             integer     not null,
    source_kind         text        not null default 'pdf'
        check (source_kind = 'pdf'),
    source_filename     text        not null,
    source_sha256       text        not null,
    extraction_method   text        not null,
    raw_text            text        not null,
    extracted_facts     jsonb       not null default '{}'::jsonb,
    corrections         jsonb       not null default '{}'::jsonb,
    evidence            jsonb       not null default '{}'::jsonb,
    readability         jsonb       not null default '{}'::jsonb,
    review_notes        text        not null default '',
    status              text        not null default 'pending_review'
        check (status in ('pending_review', 'active', 'superseded')),
    created_at          timestamptz not null default now(),
    reviewed_at         timestamptz,
    activated_at        timestamptz,
    unique (username, version),
    unique (username, source_sha256)
);

create unique index if not exists idx_resume_profiles_one_active
    on resume_profiles (username) where status = 'active';
create index if not exists idx_resume_profiles_versions
    on resume_profiles (username, version desc);


-- ---------------------------------------------------------------------------
-- job_messages — outreach messages written by the scheduled Claude routine
-- Deliberately not columns on scraped_jobs: get_scraped_jobs() does select("*")
-- over thousands of rows, and inlining message bodies would bloat every listing.
-- ---------------------------------------------------------------------------
create table if not exists job_messages (
    id              bigserial primary key,
    scraped_job_id  bigint      not null references scraped_jobs(id) on delete cascade,
    message_type    text        not null default 'cold_dm'
                                check (message_type in ('screen', 'cold_dm', 'resume_points', 'demo_html')),
    content         text        not null,
    generated_by    text        not null default 'claude-routine',
    generated_at    timestamptz not null default now(),
    profile_version integer,
    is_stale        boolean     not null default true,
    unique (scraped_job_id, message_type)
);

create index if not exists idx_job_messages_job on job_messages (scraped_job_id);
alter table job_messages add column if not exists profile_version integer;
alter table job_messages add column if not exists is_stale boolean not null default true;
create index if not exists idx_job_messages_profile on job_messages (is_stale, profile_version);


-- ---------------------------------------------------------------------------
-- cover_letter_drafts — scheduled, grounded drafts for explicitly eligible
-- high-match jobs. Each source tuple is immutable and retry-idempotent.
-- ---------------------------------------------------------------------------
create table if not exists cover_letter_drafts (
    id                  bigserial primary key,
    scraped_job_id      bigint      not null references scraped_jobs(id) on delete cascade,
    resume_profile_id   bigint      not null references resume_profiles(id),
    resume_version      integer     not null,
    jd_version          integer     not null,
    jd_hash             text        not null,
    match_score         integer     not null check (match_score between 0 and 100),
    analysis_version    text        not null,
    generation_rules_version text   not null,
    run_id              text        not null,
    content             text        not null,
    generated_by        text        not null default 'claude',
    generated_at        timestamptz not null default now(),
    is_outdated         boolean     not null default false,
    unique (scraped_job_id, resume_version, jd_hash, generation_rules_version)
);

create index if not exists idx_cover_letter_current
    on cover_letter_drafts (scraped_job_id, is_outdated, generated_at desc);


-- Atomically activate one reviewed PDF version and mark profile-dependent
-- analysis/drafts stale. SECURITY INVOKER keeps the caller's permissions.
create or replace function activate_resume_profile(
    p_profile_id bigint,
    p_username text,
    p_corrections jsonb default '{}'::jsonb,
    p_review_notes text default ''
) returns setof resume_profiles
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    target_version integer;
begin
    select version into target_version
    from resume_profiles
    where id = p_profile_id and username = p_username
    for update;

    if target_version is null then
        raise exception 'resume profile not found';
    end if;

    update resume_profiles
       set status = 'superseded'
     where username = p_username and status = 'active' and id <> p_profile_id;

    update resume_profiles
       set corrections = coalesce(p_corrections, '{}'::jsonb),
           review_notes = coalesce(p_review_notes, ''),
           status = 'active', reviewed_at = now(), activated_at = now()
     where id = p_profile_id and username = p_username;

    update scraped_jobs
       set analysis_stale = true
     where profile_version is distinct from target_version;

    update job_messages
       set is_stale = true
     where message_type in ('screen', 'cold_dm', 'resume_points')
       and profile_version is distinct from target_version;

    update cover_letter_drafts
       set is_outdated = true
     where resume_version is distinct from target_version;

    return query select * from resume_profiles where id = p_profile_id;
end;
$$;

revoke all on function activate_resume_profile(bigint, text, jsonb, text) from public, anon, authenticated;
grant execute on function activate_resume_profile(bigint, text, jsonb, text) to service_role;
grant all on table resume_profiles to service_role;
grant usage, select on sequence resume_profiles_id_seq to service_role;


-- ---------------------------------------------------------------------------
-- message_requests — freeform message requests queued from the UI
-- The app has no LLM credential; POSTing to /api/messages/* parks the request
-- here and the scheduled Claude routine writes it on its next run.
-- ---------------------------------------------------------------------------
create table if not exists message_requests (
    id            bigserial   primary key,
    message_type  text        not null,
    params        jsonb       not null default '{}'::jsonb,
    status        text        not null default 'pending',
    content       text,
    error         text,
    created_at    timestamptz not null default now(),
    completed_at  timestamptz,
    constraint message_requests_status_check
        check (status in ('pending', 'ready', 'failed'))
);

create index if not exists idx_message_requests_queue   on message_requests (status, created_at);
create index if not exists idx_message_requests_created on message_requests (created_at desc);


-- ---------------------------------------------------------------------------
-- Row Level Security
-- Enabled with no policies: anon and authenticated are denied everything, and
-- only the service_role key reaches the data. See ACCESS MODEL at the top.
-- ---------------------------------------------------------------------------
alter table applications           enable row level security;
alter table scraped_jobs           enable row level security;
alter table follow_up_history      enable row level security;
alter table company_research_cache enable row level security;
alter table mini_demos             enable row level security;
alter table notifications          enable row level security;
alter table push_subscriptions     enable row level security;
alter table user_profile           enable row level security;
alter table resume_profiles        enable row level security;
alter table job_messages           enable row level security;
alter table cover_letter_drafts    enable row level security;
alter table message_requests       enable row level security;

revoke all on table resume_profiles, cover_letter_drafts from anon, authenticated;
grant all on table resume_profiles, cover_letter_drafts to service_role;
grant usage, select on sequence resume_profiles_id_seq, cover_letter_drafts_id_seq to service_role;