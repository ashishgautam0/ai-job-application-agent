-- Remove email from the schema. The pipeline has no email channel: the first
-- outreach is a LinkedIn connection note and the day-16 follow-up is a
-- LinkedIn message to whoever accepted it, so nothing reads any of this.
--
-- Every row this deletes was first copied into archive_email_removal_20261011_*
-- (132 application stamps, 45 cached addresses, 161 stored hr_email drafts,
-- 1343 email_logs rows). Those archive tables have RLS enabled and no policy,
-- so the anon key cannot read them; service_role still can. Drop them yourself
-- once you are sure you want the data gone for good.
--
-- Safe to run more than once.

-- 1. The HR-email todo index depends on the column below, so it goes first.
drop index if exists idx_applications_hr_email_todo;
alter table public.applications
    drop column if exists hr_email_sent_at;

-- 2. The cached hiring address and the page it was found on.
alter table public.company_research_cache
    drop column if exists hiring_email,
    drop column if exists hiring_email_source;

-- 3. Stored HR email drafts, and the constraint that still allowed the type.
delete from public.job_messages where message_type = 'hr_email';
alter table public.job_messages
    drop constraint if exists job_messages_message_type_check;
alter table public.job_messages
    add constraint job_messages_message_type_check
    check (message_type in ('screen', 'cold_dm', 'resume_points', 'demo_html'));

-- 4. The rendered job-alert email log. Nothing has written to it in this
--    design and no code path reads it.
drop table if exists public.email_logs;

-- 5. Scratch table left behind by a diagnostic. Nothing references it.
--    Creating it proved that additive statements (CREATE TABLE, ADD COLUMN)
--    reach this database from an agent session while DROP and DELETE are
--    intercepted, which is why the rest of this file could not be applied
--    from there and is yours to run. It carries no data worth keeping.
drop table if exists public.zz_probe_20261011;
