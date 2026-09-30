// GENERATED FILE - do not edit by hand. Source: ai-harness/core/customer-vocabulary.yaml
// Regenerate: python prototype/scripts/compile_harness_brief.py
export const OWNER_TEXT_RULES = "CUSTOMER VOCABULARY (every customer-visible word; enforced):\n- Describe WHAT was seen and WHEN, never HOW WatchLog captured, stored, sampled, processed or reviewed it.\n- Coverage is a time window and a completeness statement (\"the cameras covered 4:39 PM to 3:35 AM\"), never a count of images.\n- Never name the technology, vendors, models, AI providers, infrastructure or internal processes behind a result.\n- Never state how many images were captured, reviewed or analysed, or how often cameras are sampled.\nNever write these words or phrases (say instead):\n- manually/visually reviewed, reviewed image-by-image -> reviewed / WatchLog reviewed the camera coverage\n- manual/visual/image-by-image review, review file -> review / WatchLog's review of the day\n- analysed images, snapshots, frames -> reviewed camera coverage\n- canonical cameras, transport, stream, onvif profiles, legacy camera rows -> cameras\n- sampling, sampled, samples, cadence, capture interval -> (omit) - describe coverage as a time window\n- snapshots, still images, video frames, frames -> what the cameras showed / a camera view\n- processing, job, review, task queue, pipeline state, failure, status, stage, processing state, attempts, backlog -> (omit)\n- ai, language, vision, llm, detection models, model name, provider, routing, model, inference providers, detector classes, confidence score, tiers, prompt -> (omit) - state what WatchLog observed and how certain it is in plain words\n- groq, ollama, cloudflare workers ai, qwen, gemma, gpt-oss, llama, rf-detr, yolo, mediapipe, compreface, onnx, supabase, coolify, seaweedfs, minio, n8n -> (omit)\n- egress, rpc, edge function, vision worker, private worker, worker id, state, status, database table, field, schemas, schema cache, service role -> (omit)";
export const CUSTOMER_VOCABULARY: { id: string; pattern: string; replaceWith: string }[] = [
 {
  "id": "review_verb",
  "pattern": "\\b(manually|visually) reviewed\\b|\\breviewed (image[- ]by[- ]image|frame[- ]by[- ]frame)\\b",
  "replaceWith": "reviewed"
 },
 {
  "id": "review_process",
  "pattern": "\\b(image[- ]by[- ]image visual review|image[- ]by[- ]image review|exported review file|manual review|visual review|review file)\\b",
  "replaceWith": "review"
 },
 {
  "id": "review_method",
  "pattern": "\\b(image[- ]by[- ]image|frame[- ]by[- ]frame)\\b",
  "replaceWith": ""
 },
 {
  "id": "analysed_images",
  "pattern": "\\banaly[sz]ed (images?|snapshots?|frames?)\\b",
  "replaceWith": "reviewed camera coverage"
 },
 {
  "id": "camera_internals",
  "pattern": "\\bcanonical cameras?\\b|\\b(transport|stream|onvif) profiles?\\b|\\blegacy (camera )?rows?\\b",
  "replaceWith": "cameras"
 },
 {
  "id": "low_cadence",
  "pattern": "\\b(low|limited|reduced|sparse) (capture |sampling |camera )?cadence\\b",
  "replaceWith": "limited coverage"
 },
 {
  "id": "capture_cadence",
  "pattern": "\\b(capture|sampling|camera|image|snapshot|frame) (cadence|interval)s?\\b",
  "replaceWith": "coverage"
 },
 {
  "id": "sampling",
  "pattern": "\\b(no )?sampling\\b|\\bsampled\\b",
  "replaceWith": ""
 },
 {
  "id": "image_samples",
  "pattern": "\\b(camera |image |visual )?samples\\b",
  "replaceWith": "camera coverage"
 },
 {
  "id": "image_units",
  "pattern": "\\b(snapshots?|still images?|video frames?|frames?)\\b",
  "replaceWith": "camera coverage"
 },
 {
  "id": "processing",
  "pattern": "\\b(processing|job|review|task) queue\\b|\\bpipeline (state|failure|status|stage)\\b|\\bprocessing (state|status|attempts?)\\b|\\bbacklog\\b",
  "replaceWith": ""
 },
 {
  "id": "ai_internals",
  "pattern": "\\b(ai|language|vision|llm|detection) models?\\b|\\bmodel (name|provider|routing)\\b|\\b(ai|model|llm|inference) providers?\\b|\\bdetector( class(es)?)?\\b|\\bconfidence (score|tier)s?\\b|\\bprompt\\b",
  "replaceWith": "WatchLog"
 },
 {
  "id": "vendors_and_tools",
  "pattern": "\\b(groq|ollama|cloudflare workers ai|qwen\\w*|gemma\\w*|gpt-oss|llama\\w*|rf-detr|yolo\\w*|mediapipe|compreface|onnx\\w*|supabase|coolify|seaweedfs|minio|n8n)\\b",
  "replaceWith": "WatchLog"
 },
 {
  "id": "infrastructure",
  "pattern": "\\b(egress|rpc|edge function|vision worker|private worker|worker (id|state|status)|database (table|field|schema)s?|schema cache|service role)\\b",
  "replaceWith": "WatchLog"
 }
];
