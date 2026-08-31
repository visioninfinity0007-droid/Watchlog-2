# Runbook — Add a tenant manually

**When:** onboarding a customer without the self-serve flow (e.g. a pilot you set up for them,
or a support recovery). Normal customers self-serve: marketing → **Start free** → `/signup` →
`/onboarding` (which calls `wl_bootstrap_tenant`). Use this only for ops.

**Who can run it:** anyone with the Supabase DB password (postgres role) from
`projects/watchlog/.env`. Connect with the migration runner's credentials.

**Model:** `tenants` ← `memberships` (owner) ← `sites` ← `enrollment_codes`. A tenant is useless
until a person (an `auth.users` row) owns it and a site has an enrollment code for the agent.

---

## Option A — customer will sign in themselves (preferred)

1. Ask the customer to sign up at `https://watchlog.<domain>/signup` (creates their `auth.users` row,
   email-confirmed).
2. On their first login the portal calls `wl_bootstrap_tenant(company, site, tz)` which creates the
   tenant + owner membership + first site + a 14-day enrollment code, idempotently. Nothing else to do.

## Option B — fully manual (ops-created), customer already has a login

Run as the postgres role (psql or the helper below). Replace the email and names.

```sql
-- 1. find the customer's user id (they must have signed up at least once)
select id, email from auth.users where lower(email) = lower('owner@example.com');

-- 2. create everything in one transaction, owner-scoped
do $$
declare v_user uuid; v_tenant uuid; v_site uuid; v_code text;
begin
  select id into v_user from auth.users where lower(email)=lower('owner@example.com');
  if v_user is null then raise exception 'no such user — have them sign up first'; end if;

  insert into tenants (name) values ('Acme Security') returning id into v_tenant;
  insert into memberships (user_id, tenant_id, role) values (v_user, v_tenant, 'owner');
  insert into sites (tenant_id, name, timezone)
    values (v_tenant, 'Head Office', 'Asia/Karachi') returning id into v_site;

  v_code := 'WL-' || upper(substr(replace(gen_random_uuid()::text,'-',''),1,4))
                  || '-' || upper(substr(replace(gen_random_uuid()::text,'-',''),1,4));
  insert into enrollment_codes (code, tenant_id, site_id, expires_at)
    values (v_code, v_tenant, v_site, now() + interval '14 days');

  raise notice 'tenant=%  site=%  enrollment_code=%', v_tenant, v_site, v_code;
end $$;
```

The enrollment code printed by the `raise notice` is what the customer types into the installer.

## Helper (from the repo)

```bash
python prototype/supabase/mint_code.py            # issues a fresh code for an existing site
```

---

## Set the tenant's plan (ops)

Customers cannot mark themselves paid (see `KEY_ROTATION.md`/billing). To set a plan/subscription
status as ops, call the authoritative writer as postgres — it is granted to no client role:

```sql
select wl_billing_set_subscription('<tenant-uuid>', 'starter', 'active');
```

## Verify

```sql
select t.name, m.role, u.email, s.name as site, e.code
from tenants t
join memberships m on m.tenant_id=t.id
join auth.users u on u.id=m.user_id
join sites s on s.tenant_id=t.id
left join enrollment_codes e on e.site_id=s.id and e.used_at is null
where t.id='<tenant-uuid>';
```

Then confirm isolation is intact (a new tenant must not see others):

```bash
python prototype/tests/test_tenant_isolation.py   # must stay 9/9
```

## Rollback / remove

See `TENANT_OFFBOARDING.md` (delete cascades from `tenants`). Do **not** hand-delete `auth.users`
if the person owns other tenants.
