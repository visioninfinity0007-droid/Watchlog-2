"use client";

import { createClient } from "@supabase/supabase-js";

// The publishable key is public by design and is safe in a browser
// bundle. It grants nothing on its own: every table is behind RLS, and a
// signed-in user only ever sees rows belonging to a tenant they are a
// member of. Authorisation lives in Postgres, not in this file.
export const SUPABASE_URL =
  process.env.NEXT_PUBLIC_SUPABASE_URL || "";
export const SUPABASE_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || "";

let client;

export function supabase() {
  if (!client) {
    client = createClient(SUPABASE_URL, SUPABASE_KEY, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    });
  }
  return client;
}

/** Readable message from a Supabase/Postgres error. */
export function say(error) {
  if (!error) return "";
  const m = error.message || String(error);
  if (/invalid login credentials/i.test(m)) return "Wrong email or password.";
  if (/email not confirmed/i.test(m))
    return "Check your email and confirm the address, then sign in.";
  if (/user already registered/i.test(m))
    return "That email already has an account. Sign in instead.";
  if (/password should be at least/i.test(m))
    return "Password must be at least 6 characters.";
  return m;
}
