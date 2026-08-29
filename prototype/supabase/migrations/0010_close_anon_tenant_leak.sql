-- =====================================================================
-- Close the prototype's anon-readable, all-tenant surface.
--
-- 0009 left this block commented with the note "RUN THIS BEFORE THE
-- SECOND TENANT EXISTS", because running it breaks the standalone viewer
-- that was then the live demo.
--
-- That condition has been breached. As of 2026-08-29 there are two
-- tenants - 'AKSS (prototype)' and 'Demo Security Co' - and the leak was
-- reproduced over real HTTP using nothing but the public publishable key:
--
--     POST /rest/v1/rpc/wl_fleet   ->  10 rows spanning BOTH tenants
--
-- No login, no session. Any visitor who reads the key out of the
-- JavaScript bundle - which is exactly what a publishable key is for -
-- could read every tenant's sites, cameras, events and incident stills.
--
-- wl_prune_snapshots was worse than a read leak: it DELETES rows, and it
-- was callable by anon too.
--
-- This directly blocks two things the scope of work commits to:
--   Milestone 2 - "tenant isolation at the database level"
--   Milestone 4 - "tenant isolation QA test suite (hard gate)"
--
-- What replaces it: wl_portal_overview / wl_portal_snapshot, which are
-- granted to `authenticated` only and scope every query through
-- wl_my_tenant(). The portal already uses those exclusively.
--
-- KNOWN CONSEQUENCE, accepted deliberately: the standalone viewer reads
-- wl_fleet / wl_analytics / wl_site_health / wl_recent_events /
-- wl_get_snapshot anonymously, so it stops returning data after this
-- runs. It is a prototype dashboard superseded by the portal. Leaving
-- customer data world-readable to keep a demo working is not a trade
-- worth making.
--
-- The agent-facing functions (wl_enroll, wl_heartbeat, wl_sync_cameras,
-- wl_ingest_events) stay granted to anon ON PURPOSE - they authenticate
-- the caller by agent key inside the function body, which is the
-- documented deviation from the service-role design.
--
-- Reversible: re-grant execute to anon on any function below.
-- =====================================================================

revoke execute on function public.wl_fleet()                    from anon;
revoke execute on function public.wl_recent_events(int)         from anon;
revoke execute on function public.wl_analytics(int)             from anon;
revoke execute on function public.wl_site_health(int)           from anon;
revoke execute on function public.wl_daily_report(uuid, date)   from anon;
revoke execute on function public.wl_get_snapshot(bigint)       from anon;

-- Destructive, and never had any business being anon-callable.
revoke execute on function public.wl_prune_snapshots(int, int)  from anon;
revoke execute on function public.wl_prune_snapshots(int, int)  from authenticated;
