/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export. The portal talks to Supabase straight from the
  // browser, and every table is behind RLS scoped by the signed-in
  // user's tenant - so authorisation is enforced by the database, not by
  // a server we would otherwise have to run, secure and keep patched.
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};
export default nextConfig;
