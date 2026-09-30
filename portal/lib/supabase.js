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

/** Customer-safe error message. Internal database/infrastructure detail never reaches the portal. */
export function say(error) {
  if (!error) return "";
  const m = String(error.message || error || "").trim();
  if (/invalid login credentials/i.test(m)) return "Wrong email or password.";
  if (/email not confirmed/i.test(m))
    return "Check your email and confirm the address, then sign in.";
  if (/user already registered/i.test(m))
    return "That email already has an account. Sign in instead.";
  if (/password should be at least/i.test(m))
    return "Password must be at least 6 characters.";
  if (/canceling statement|statement timeout|timeout.*statement/i.test(m))
    return "WatchLog took too long to load this information. Try again.";
  if (/failed to fetch|network.*error|network request failed|load failed/i.test(m))
    return "WatchLog could not reach the service. Check your connection and try again.";
  if (/not authorized|permission denied|insufficient privilege|42501/i.test(m))
    return "You do not have access to this action.";
  if (/duplicate key|unique constraint/i.test(m))
    return "That item already exists.";
  if (
    /schema cache|function .* does not exist|relation .* does not exist|column .* does not exist|syntax error|sqlstate|pgrst\d+|postgres|supabase|violates .* constraint|internal server error/i.test(m)
  )
    return "WatchLog could not complete that request. Try again.";
  return "WatchLog could not complete that request. Try again.";
}
