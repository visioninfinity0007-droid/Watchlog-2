// Sign a release manifest exactly as prototype/agent/updater.py verifies it.
import { canonicalManifest } from "./canonical.ts";

// Returns a plain ArrayBuffer: a BufferSource under every Deno/TypeScript lib, including the
// stricter Uint8Array<ArrayBufferLike> typing that rejects a Uint8Array in importKey.
function b64ToBytes(b64: string): ArrayBuffer {
  const raw = atob(b64.trim());
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return buf;
}

function bytesToB64(bytes: ArrayBuffer): string {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s);
}

export async function buildSignedManifest(
  body: Record<string, unknown>, privatePkcs8B64: string, now = new Date(),
): Promise<Record<string, unknown>> {
  const manifest: Record<string, unknown> = {
    ...body,
    generated_at: now.toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
  const key = await crypto.subtle.importKey("pkcs8", b64ToBytes(privatePkcs8B64), { name: "Ed25519" }, false, ["sign"]);
  const signature = await crypto.subtle.sign("Ed25519", key, new TextEncoder().encode(canonicalManifest(manifest)));
  return { ...manifest, signature: bytesToB64(signature) };
}
