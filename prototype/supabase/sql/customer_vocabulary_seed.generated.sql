-- GENERATED from ai-harness/core/customer-vocabulary.yaml - do not edit by hand.
-- Regenerate: python prototype/scripts/compile_harness_brief.py ; apply this file after any vocabulary change.
insert into public.customer_vocabulary_rules(id, ord, pattern_pg, replace_with) values
  ('review_verb', 1, '\y(manually|visually) reviewed\y|\yreviewed (image[- ]by[- ]image|frame[- ]by[- ]frame)\y', 'reviewed'),
  ('review_process', 2, '\y(image[- ]by[- ]image visual review|image[- ]by[- ]image review|exported review file|manual review|visual review|review file)\y', 'review'),
  ('review_method', 3, '\y(image[- ]by[- ]image|frame[- ]by[- ]frame)\y', ''),
  ('analysed_images', 4, '\yanaly[sz]ed (images?|snapshots?|frames?)\y', 'reviewed camera coverage'),
  ('camera_internals', 5, '\ycanonical cameras?\y|\y(transport|stream|onvif) profiles?\y|\ylegacy (camera )?rows?\y', 'cameras'),
  ('low_cadence', 6, '\y(low|limited|reduced|sparse) (capture |sampling |camera )?cadence\y', 'limited coverage'),
  ('capture_cadence', 7, '\y(capture|sampling|camera|image|snapshot|frame) (cadence|interval)s?\y', 'coverage'),
  ('sampling', 8, '\y(no )?sampling\y|\ysampled\y', ''),
  ('image_samples', 9, '\y(camera |image |visual )?samples\y', 'camera coverage'),
  ('image_units', 10, '\y(snapshots?|still images?|video frames?|frames?)\y', 'camera coverage'),
  ('processing', 11, '\y(processing|job|review|task) queue\y|\ypipeline (state|failure|status|stage)\y|\yprocessing (state|status|attempts?)\y|\ybacklog\y', ''),
  ('ai_internals', 12, '\y(ai|language|vision|llm|detection) models?\y|\ymodel (name|provider|routing)\y|\y(ai|model|llm|inference) providers?\y|\ydetector( class(es)?)?\y|\yconfidence (score|tier)s?\y|\yprompt\y', 'WatchLog'),
  ('vendors_and_tools', 13, '\y(groq|ollama|cloudflare workers ai|qwen\w*|gemma\w*|gpt-oss|llama\w*|rf-detr|yolo\w*|mediapipe|compreface|onnx\w*|supabase|coolify|seaweedfs|minio|n8n)\y', 'WatchLog'),
  ('infrastructure', 14, '\y(egress|rpc|edge function|vision worker|private worker|worker (id|state|status)|database (table|field|schema)s?|schema cache|service role)\y', 'WatchLog')
on conflict (id) do update set ord = excluded.ord, pattern_pg = excluded.pattern_pg, replace_with = excluded.replace_with;
delete from public.customer_vocabulary_rules where id not in ('review_verb', 'review_process', 'review_method', 'analysed_images', 'camera_internals', 'low_cadence', 'capture_cadence', 'sampling', 'image_samples', 'image_units', 'processing', 'ai_internals', 'vendors_and_tools', 'infrastructure');
-- internal keys exempt from rewriting (mirrored in wl_customer_json_clean): 'review_audit', 'internal', 'internal_notes', 'internal_audit', 'audit', '_internal', 'trace', 'debug', 'provenance'
