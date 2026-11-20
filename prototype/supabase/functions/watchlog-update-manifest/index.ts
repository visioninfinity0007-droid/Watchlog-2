// watchlog-update-manifest — WatchLog's signed Agent update channel (migration 0236).
//
// GET returns the release manifest the Agents' updater verifies (prototype/agent/updater.py):
//   {"schema": "watchlog.release_manifest.v1", "channels": {...}, "generated_at": ..., "signature": <b64>}
// The signature is a detached Ed25519 signature over canonicalManifest() with the key pair the
// fielded Agents already trust (vault; wl_get_update_signing_key, service_role only). The private
// key is read inside this function and never leaves the server. Public and read-only: the Agent
// calls it with no credentials (VERIFY_JWT=false), and it can only describe releases that were
// published into agent_release_channels; each Agent still checks the package's SHA-256 and size.
import { buildSignedManifest } from "./sign.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

async function rpc(name: string): Promise<unknown> {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}`, "Content-Type": "application/json" },
    body: "{}",
  });
  if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
  return await r.json();
}

function reply(status: number, body: unknown): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

Deno.serve(async (req) => {
  if (req.method !== "GET" && req.method !== "HEAD") return reply(405, { error: "method not allowed" });
  if (!SUPABASE_URL || !SERVICE_KEY) return reply(503, { error: "manifest signing unavailable" });
  try {
    const [body, key] = await Promise.all([
      rpc("wl_service_agent_release_manifest") as Promise<Record<string, unknown>>,
      rpc("wl_get_update_signing_key") as Promise<{ private_pkcs8_b64?: string }>,
    ]);
    if (!key?.private_pkcs8_b64) return reply(503, { error: "manifest signing unavailable" });
    if (!body || typeof body !== "object" || !body.channels) return reply(503, { error: "no releases published" });
    return reply(200, await buildSignedManifest(body, key.private_pkcs8_b64));
  } catch (_e) {
    // Never echo internals (keys, URLs) to a public caller.
    return reply(503, { error: "manifest unavailable" });
  }
});
