-- ===================================================================
-- 0053 — Rule governance setter. wl_upsert_monitoring_rule_v2 (0030) sets the
-- core monitoring rule (type, geometry, schedule, classes, severity); this sets
-- the Operations-Intelligence GOVERNANCE layer added in 0049 (confidence gate,
-- cooldown de-dupe, sensitive/review handling, configured actions, evidence
-- requirements) on an existing rule. Owner/admin, tenant-scoped. Writing these
-- through an RPC (RLS on monitoring_rules is select-only) keeps the portal from
-- fabricating capability, and the 0049 triggers version+snapshot the change.
-- ===================================================================

create or replace function public.wl_set_rule_governance(
  p_rule_id        uuid,
  p_confidence_min numeric default null,
  p_cooldown_seconds int   default null,
  p_actions        jsonb   default null,
  p_evidence_json  jsonb   default null,
  p_sensitive      boolean default null,
  p_review_required boolean default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tenant uuid := wl_require_role(array['owner','admin']);
  v_row    public.monitoring_rules;
begin
  if p_actions is not null and jsonb_typeof(p_actions) <> 'array' then
    raise exception 'actions must be a JSON array' using errcode = '22023';
  end if;
  if p_confidence_min is not null and (p_confidence_min < 0 or p_confidence_min > 1) then
    raise exception 'confidence_min must be between 0 and 1' using errcode = '22023';
  end if;
  if p_cooldown_seconds is not null and (p_cooldown_seconds < 0 or p_cooldown_seconds > 86400) then
    raise exception 'cooldown_seconds must be between 0 and 86400' using errcode = '22023';
  end if;
  -- The editor always sends the FULL governance state, so we SET directly (a null
  -- confidence/cooldown means "no threshold / no cooldown" — a valid, clearable state).
  update public.monitoring_rules
     set confidence_min   = p_confidence_min,
         cooldown_seconds  = p_cooldown_seconds,
         actions           = coalesce(p_actions, '[]'::jsonb),
         evidence_json     = coalesce(p_evidence_json, '{}'::jsonb),
         sensitive         = coalesce(p_sensitive, false),
         review_required   = coalesce(p_review_required, false)
   where id = p_rule_id and tenant_id = v_tenant and rule_type <> 'health'
   returning * into v_row;
  if v_row.id is null then
    raise exception 'rule not found in your account' using errcode = '42704';
  end if;
  return to_jsonb(v_row);
end $$;

revoke all on function public.wl_set_rule_governance(uuid,numeric,int,jsonb,jsonb,boolean,boolean) from public, anon;
grant execute on function public.wl_set_rule_governance(uuid,numeric,int,jsonb,jsonb,boolean,boolean) to authenticated;
