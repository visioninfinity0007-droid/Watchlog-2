-- =====================================================================
-- 0018 — RLS / grant hardening (P1, SOW invariant C: minimize PUBLIC grants)
--
-- 1. Four member-read policies (invitations, push_sources, report_deliveries,
--    report_recipients) were attached to the `public` role. Their USING
--    clause is wl_is_member(tenant_id), so anon already saw nothing — but
--    the grant surface should match the other eight policies. Re-scope them
--    to `authenticated`. Behaviour for real users is identical; anon still
--    sees nothing.
--
-- 2. anon AND authenticated held direct INSERT/UPDATE/DELETE on the app
--    tables. RLS denied those writes (no write policy), but the grant should
--    not exist: every write in WatchLog goes through a SECURITY DEFINER
--    function, which runs as the table owner and is unaffected. Strip the
--    direct write grants from both app roles on every table as defence in
--    depth. (schema_migrations is handled in 0016.)
--
-- Idempotent. Rollback: recreate the policies `to public` and re-grant.
-- =====================================================================

do $$
declare p record;
begin
  for p in
    select tablename, policyname from pg_policies
     where schemaname = 'public'
       and policyname in ('portal_read_invitations','portal_read_push_sources',
                          'portal_read_deliveries','portal_read_recipients')
  loop
    execute format('drop policy %I on public.%I', p.policyname, p.tablename);
  end loop;
end $$;

create policy portal_read_invitations on public.invitations
  for select to authenticated using (public.wl_is_member(tenant_id));
create policy portal_read_push_sources on public.push_sources
  for select to authenticated using (public.wl_is_member(tenant_id));
create policy portal_read_deliveries on public.report_deliveries
  for select to authenticated using (public.wl_is_member(tenant_id));
create policy portal_read_recipients on public.report_recipients
  for select to authenticated using (public.wl_is_member(tenant_id));

do $$
declare t text;
begin
  for t in select tablename from pg_tables
            where schemaname = 'public' and tablename <> 'schema_migrations'
  loop
    execute format('revoke insert, update, delete on public.%I from anon, authenticated', t);
  end loop;
end $$;
