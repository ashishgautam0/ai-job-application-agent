-- Store the Cold DM recipient on its own history row.
--
-- The recipient's name and LinkedIn URL were only ever kept inside
-- message_content, as a "Sent to <name>: <url>" trailer the desktop agent
-- appends. That made them unqueryable, and a note recorded by hand through the
-- Tracker form carried no trailer at all, so _connection_recipient found
-- nothing and the follow-up draft kept its placeholder.
--
-- These columns already exist on the live database (added out-of-band and never
-- declared here). The add-column statements are for any database built from
-- schema.sql before today; they are a no-op where the columns are present.
--
-- Safe to run more than once.

alter table public.follow_up_history
    add column if not exists created_at            timestamptz not null default now(),
    add column if not exists recipient_name        text,
    add column if not exists recipient_profile_url text,
    add column if not exists accepted_at           timestamptz;

-- Backfill from the text that is already there. coalesce(nullif(...)) keeps this
-- re-runnable and stops it overwriting a value a newer record wrote directly.
update public.follow_up_history
   set recipient_profile_url = coalesce(
           nullif(recipient_profile_url, ''),
           substring(message_content from '(https?://[a-zA-Z0-9.\-]*linkedin\.com/in/[^\s)]+)')),
       recipient_name = coalesce(
           nullif(recipient_name, ''),
           substring(message_content from 'Sent to ([^:]+):'))
 where channel = 'LinkedIn connection';
