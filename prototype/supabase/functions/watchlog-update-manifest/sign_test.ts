// deno test prototype/supabase/functions/watchlog-update-manifest/sign_test.ts
// The update channel signs bytes the Agent verifies: canonicalManifest must equal
// prototype/agent/updater.py canonical_manifest_bytes (pinned below and in
// prototype/tests/test_update_manifest_channel.py), and the Ed25519 signature must verify.
import { assert, assertEquals, assertThrows } from "https://deno.land/std@0.224.0/assert/mod.ts";
import { canonicalManifest } from "./canonical.ts";
import { buildSignedManifest } from "./sign.ts";

export const FIXTURE = {
  schema: "watchlog.release_manifest.v1",
  generated_at: "2026-11-06T16:00:00Z",
  signature: "ignored",
  channels: {
    production: {
      version: "5.1.13", url: "https://example.test/a/watchlog-agent.exe", sha256: "ab".repeat(32),
      size: 91557259, update_class: "AGENT_ONLY_COMPATIBLE", min_installed_components: "5.1.0",
      notes: 'Café "sharp" views / update',
    },
  },
};
export const PINNED =
  '{"channels":{"production":{"min_installed_components":"5.1.0","notes":"Caf\\u00e9 \\"sharp\\" views / update",' +
  '"sha256":"abababababababababababababababababababababababababababababababab","size":91557259,' +
  '"update_class":"AGENT_ONLY_COMPATIBLE","url":"https://example.test/a/watchlog-agent.exe","version":"5.1.13"}},' +
  '"generated_at":"2026-11-06T16:00:00Z","schema":"watchlog.release_manifest.v1"}';

Deno.test("canonical bytes equal the Agent's (sorted keys, compact, ASCII escapes, no signature)", () => {
  assertEquals(canonicalManifest(FIXTURE), PINNED);
  assertThrows(() => canonicalManifest({ channels: { x: { size: 1.5 } } }));
});

Deno.test("the signed manifest verifies with the matching public key and fails when changed", async () => {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]) as CryptoKeyPair;
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey("pkcs8", pair.privateKey));
  const b64 = btoa(String.fromCharCode(...pkcs8));
  const { signature: _s, generated_at: _g, ...body } = FIXTURE;
  const signed = await buildSignedManifest(body, b64, new Date("2026-11-06T16:00:00.123Z"));
  assertEquals(signed.generated_at, "2026-11-06T16:00:00Z");
  const sig = Uint8Array.from(atob(String(signed.signature)), (c) => c.charCodeAt(0));
  const ok = await crypto.subtle.verify("Ed25519", pair.publicKey, sig, new TextEncoder().encode(canonicalManifest(signed)));
  assert(ok, "signature verifies");
  const tampered = structuredClone(signed) as typeof signed & { channels: { production: { sha256: string } } };
  tampered.channels.production.sha256 = "cd".repeat(32);
  const bad = await crypto.subtle.verify("Ed25519", pair.publicKey, sig, new TextEncoder().encode(canonicalManifest(tampered)));
  assert(!bad, "a changed package hash breaks the signature");
});
