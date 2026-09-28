-- Production schedule for the privacy-gated vision worker.
-- The one-frame-per-minute cadence stays inside the currently configured
-- external vision provider's observed output-token limit. Increase only after
-- provider capacity is raised or a local/private vision provider is configured.

do $$
begin
  if not exists (select 1 from vault.secrets where name='wl_project_url') then
    perform vault.create_secret(
      'https://oyvgubyxmjlijiczjona.supabase.co',
      'wl_project_url',
      'WatchLog production Supabase project URL for scheduled Edge Functions'
    );
  end if;
  if exists (select 1 from cron.job where jobname='watchlog-vision-worker') then
    perform cron.unschedule('watchlog-vision-worker');
  end if;
end $$;

select cron.schedule(
  'watchlog-vision-worker',
  '* * * * *',
  $job$
  select net.http_post(
    url := (select decrypted_secret from vault.decrypted_secrets where name='wl_project_url' limit 1)
      || '/functions/v1/watchlog-vision-worker',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'x-watchlog-worker-secret',
      (select decrypted_secret from vault.decrypted_secrets where name='wl_vision_worker_cron_secret' limit 1)
    ),
    body := '{"limit":1}'::jsonb,
    timeout_milliseconds := 120000
  ) as request_id;
  $job$
);
