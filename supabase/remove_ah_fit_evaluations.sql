-- Retire the per-job A-H evaluation artifact and prevent it being recreated.
delete from public.job_messages where message_type = 'evaluation';

alter table public.job_messages
    drop constraint if exists job_messages_message_type_check;

alter table public.job_messages
    add constraint job_messages_message_type_check
    check (message_type in ('screen', 'cold_dm', 'resume_points', 'demo_html'));

create or replace function public.activate_resume_profile(
    p_profile_id bigint,
    p_username text,
    p_corrections jsonb default '{}'::jsonb,
    p_review_notes text default ''
) returns setof public.resume_profiles
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    target_version integer;
begin
    select version into target_version
    from public.resume_profiles
    where id = p_profile_id and username = p_username
    for update;

    if target_version is null then
        raise exception 'resume profile not found';
    end if;

    update public.resume_profiles
       set status = 'superseded'
     where username = p_username and status = 'active' and id <> p_profile_id;

    update public.resume_profiles
       set corrections = coalesce(p_corrections, '{}'::jsonb),
           review_notes = coalesce(p_review_notes, ''),
           status = 'active', reviewed_at = now(), activated_at = now()
     where id = p_profile_id and username = p_username;

    update public.scraped_jobs
       set analysis_stale = true
     where profile_version is distinct from target_version;

    update public.job_messages
       set is_stale = true
     where message_type in ('screen', 'cold_dm', 'resume_points')
       and profile_version is distinct from target_version;

    update public.cover_letter_drafts
       set is_outdated = true
     where resume_version is distinct from target_version;

    return query select * from public.resume_profiles where id = p_profile_id;
end;
$$;

revoke all on function public.activate_resume_profile(bigint, text, jsonb, text)
    from public, anon, authenticated;
grant execute on function public.activate_resume_profile(bigint, text, jsonb, text)
    to service_role;
