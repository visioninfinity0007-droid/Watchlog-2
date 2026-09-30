-- 0141 — Customer-language safety net for everything WatchLog shows a customer.
--
-- Reports and visual day summaries are written by several authors: the automatic daily report job,
-- the vision workers, and people or AI assistants publishing reviewed reports. On 2026-09-28/29
-- client reports went out containing internal wording ("snapshots", "frames manually reviewed",
-- "canonical cameras", "no sampling", "exported review file").
--
-- The rule lives in ai-harness/core/customer-vocabulary.yaml. This migration enforces it at the only
-- place every author must pass through: the database write.
--   * customer_vocabulary_rules: the ordered rewrite rules, seeded from the harness
--     (prototype/supabase/sql/customer_vocabulary_seed.generated.sql re-applies them after changes).
--   * wl_customer_text_clean / wl_customer_json_clean: rewrite every customer-visible string. Internal
--     audit keys are exempt.
--   * BEFORE INSERT/UPDATE triggers on report_snapshots.payload and visual_day_summaries.summary.
--     The triggers REWRITE and never reject, so no automated job can be broken. Each rewrite is noted in
--     the payload's internal review_audit.
-- Authors should still write customer language in the first place (ai-harness/core/customer-language.md);
-- this is the safety net, not the style guide.

create table if not exists public.customer_vocabulary_rules (
  id           text primary key,
  ord          integer not null,
  pattern_pg   text not null,          -- Postgres ARE, case-insensitive, \y word boundaries
  replace_with text not null default ''
);
alter table public.customer_vocabulary_rules enable row level security;
revoke all on public.customer_vocabulary_rules from anon, authenticated;

insert into public.customer_vocabulary_rules(id, ord, pattern_pg, replace_with) values
  ('review_process', 1, '\y(manually reviewed|manual review|visual review|image[- ]by[- ]image|exported review file|review file)\y', 'reviewed'),
  ('analysed_images', 2, '\yanaly[sz]ed (images?|snapshots?|frames?)\y', 'reviewed camera coverage'),
  ('camera_internals', 3, '\ycanonical cameras?\y|\y(transport|stream|onvif) profiles?\y|\ylegacy (camera )?rows?\y', 'cameras'),
  ('sampling', 4, '\y(no )?sampling\y|\ysampled\y|\ycapture (interval|cadence)\y', ''),
  ('image_units', 5, '\y(snapshots?|still images?|video frames?|frames?)\y', 'camera coverage'),
  ('processing', 6, '\y(processing|job|review|task) queue\y|\ypipeline (state|failure|status|stage)\y|\yprocessing (state|status|attempts?)\y|\ybacklog\y', ''),
  ('ai_internals', 7, '\y(ai|language|vision|llm|detection) models?\y|\ymodel (name|provider|routing)\y|\y(ai|model|llm|inference) providers?\y|\ydetector( class(es)?)?\y|\yconfidence (score|tier)s?\y|\yprompt\y', 'WatchLog'),
  ('vendors_and_tools', 8, '\y(groq|ollama|cloudflare workers ai|qwen\w*|gemma\w*|gpt-oss|llama\w*|rf-detr|yolo\w*|mediapipe|compreface|onnx\w*|supabase|coolify|seaweedfs|minio|n8n)\y', 'WatchLog'),
  ('infrastructure', 9, '\y(egress|rpc|edge function|vision worker|private worker|worker (id|state|status)|database (table|field|schema)s?|schema cache|service role)\y', 'WatchLog')
on conflict (id) do update set ord = excluded.ord, pattern_pg = excluded.pattern_pg, replace_with = excluded.replace_with;
delete from public.customer_vocabulary_rules where id not in ('review_process', 'analysed_images', 'camera_internals', 'sampling', 'image_units', 'processing', 'ai_internals', 'vendors_and_tools', 'infrastructure');

create or replace function public.wl_customer_text_clean(p text)
returns text language plpgsql stable set search_path = public as $$
declare r record; s text := p;
begin
  if s is null or s = '' then return s; end if;
  for r in select pattern_pg, replace_with from customer_vocabulary_rules order by ord loop
    s := regexp_replace(s, r.pattern_pg, r.replace_with, 'gi');
  end loop;
  if s is distinct from p then   -- tidy only rewritten strings; never touch clean text or line breaks
    s := regexp_replace(s, '[ \t]{2,}', ' ', 'g');
    s := regexp_replace(s, '[ \t]+([,.;:])', '\1', 'g');
    s := regexp_replace(s, '([;,])[ \t]*([;,.])', '\2', 'g');
    s := btrim(s, ' ;,');
  end if;
  return s;
end $$;

create or replace function public.wl_customer_json_clean(p jsonb)
returns jsonb language plpgsql stable set search_path = public as $$
declare k text; v jsonb; out jsonb;
begin
  if p is null then return p; end if;
  case jsonb_typeof(p)
    when 'object' then
      out := '{}'::jsonb;
      for k, v in select key, value from jsonb_each(p) loop
        out := out || jsonb_build_object(k, case when k = any(array['review_audit', 'internal', 'internal_notes', 'internal_audit', 'audit', '_internal', 'trace', 'debug', 'provenance']::text[]) then v else wl_customer_json_clean(v) end);
      end loop;
      return out;
    when 'array' then
      select coalesce(jsonb_agg(wl_customer_json_clean(e) order by n), '[]'::jsonb) into out
        from jsonb_array_elements(p) with ordinality as t(e, n);
      return out;
    when 'string' then
      return to_jsonb(wl_customer_text_clean(p #>> '{}'));
    else
      return p;
  end case;
end $$;

create or replace function public.wl_guard_customer_report_text()
returns trigger language plpgsql set search_path = public as $$
declare cleaned jsonb := wl_customer_json_clean(new.payload);
begin
  if cleaned is distinct from new.payload then
    new.payload := jsonb_set(
      case when jsonb_typeof(cleaned->'review_audit') = 'object' then cleaned
           else cleaned || jsonb_build_object('review_audit', coalesce(cleaned->'review_audit', '{}'::jsonb)) end,
      '{review_audit,customer_language_rewritten_at}', to_jsonb(now()), true);
    raise notice 'WatchLog customer-language safety net rewrote internal wording in report % (%). Write customer language at the source: ai-harness/core/customer-language.md', new.id, new.report_date;
  end if;
  return new;
end $$;

create or replace function public.wl_guard_customer_visual_summary_text()
returns trigger language plpgsql set search_path = public as $$
begin
  new.summary := wl_customer_json_clean(new.summary);
  return new;
end $$;

drop trigger if exists wl_guard_customer_report_text on public.report_snapshots;
create trigger wl_guard_customer_report_text
  before insert or update of payload on public.report_snapshots
  for each row execute function public.wl_guard_customer_report_text();

drop trigger if exists wl_guard_customer_visual_summary_text on public.visual_day_summaries;
create trigger wl_guard_customer_visual_summary_text
  before insert or update of summary on public.visual_day_summaries
  for each row execute function public.wl_guard_customer_visual_summary_text();

revoke all on function public.wl_customer_text_clean(text) from public, anon;
revoke all on function public.wl_customer_json_clean(jsonb) from public, anon;
grant execute on function public.wl_customer_text_clean(text) to authenticated, service_role;
grant execute on function public.wl_customer_json_clean(jsonb) to authenticated, service_role;
