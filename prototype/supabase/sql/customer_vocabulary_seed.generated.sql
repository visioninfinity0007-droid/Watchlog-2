-- GENERATED from ai-harness/core/customer-vocabulary.yaml - do not edit by hand.
-- Regenerate: python prototype/scripts/compile_harness_brief.py ; apply this file after any vocabulary change.
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
-- internal keys exempt from rewriting (mirrored in wl_customer_json_clean): 'review_audit', 'internal', 'internal_notes', 'internal_audit', 'audit', '_internal', 'trace', 'debug', 'provenance'
