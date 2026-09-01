#!/usr/bin/env bash
#
# WatchLog marketing site content (revamp).
#
# Idempotent: creates the pages the theme's slug-based templates render, sets
# the front page, and applies site options. Page BODIES live in the templates
# (page-<slug>.php) under version control - not in the database - so design is
# reviewable in git. Nested solution pages are created as children of
# /solutions/ so their URL path is /solutions/<child>/ (WP still resolves the
# template by the child slug, e.g. page-retail.php).
#
# Navigation is rendered directly by header.php/footer.php (a designed
# mega-menu), so no WP nav menus are built here.
#
# Run inside the WordPress container:
#     docker exec <container> bash /usr/src/wordpress/site-content.sh
#
set -eu   # no pipefail: the container shell may be dash
cd /var/www/html
W="wp --allow-root"

say() { echo "  $*" >&2; }   # stderr: stdout carries ids

# --- clear WordPress's stock content --------------------------------
IDS=$($W post list --post_type=post --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true
IDS=$($W post list --post_type=page --title="Sample Page" --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true

# --- helpers ---------------------------------------------------------
# ensure_page <slug> <title> [parent_id]
ensure_page() {
  local slug="$1" title="$2" parent="${3:-0}" id
  id=$($W post list --post_type=page --name="$slug" --format=ids 2>/dev/null || true)
  if [ -z "$id" ]; then
    id=$($W post create --post_type=page --post_status=publish \
          --post_name="$slug" --post_title="$title" --post_parent="$parent" \
          --post_content="" --porcelain)
    say "created /$slug ($id)"
  else
    $W post update "$id" --post_title="$title" --post_parent="$parent" >/dev/null
    say "updated /$slug ($id)"
  fi
  echo "$id"
}

# --- home ------------------------------------------------------------
HOME_ID=$(ensure_page home "Home")

# --- product ---------------------------------------------------------
ensure_page platform    "Platform"    >/dev/null
ensure_page incidents   "Incidents"   >/dev/null
ensure_page reporting   "Reporting"   >/dev/null
ensure_page site-health "Site Health" >/dev/null

# --- solutions (parent + children) ----------------------------------
SOL_ID=$(ensure_page solutions "Solutions")
ensure_page warehouses-logistics "Warehouses & Logistics" "$SOL_ID" >/dev/null
ensure_page retail               "Retail"                  "$SOL_ID" >/dev/null
ensure_page manufacturing        "Manufacturing"           "$SOL_ID" >/dev/null
ensure_page schools-campuses     "Schools & Campuses"      "$SOL_ID" >/dev/null
ensure_page offices              "Offices & Commercial"    "$SOL_ID" >/dev/null

# --- trust / how / commercial / legal -------------------------------
ensure_page how-it-works  "How it works"  >/dev/null
ensure_page compatibility "Compatibility" >/dev/null
ensure_page security      "Security"      >/dev/null
ensure_page pricing       "Pricing"       >/dev/null
ensure_page setup         "Setup"         >/dev/null
ensure_page contact       "Contact"       >/dev/null
ensure_page privacy       "Privacy"       >/dev/null
ensure_page terms         "Terms"         >/dev/null

# --- settings --------------------------------------------------------
$W option update show_on_front page >/dev/null
$W option update page_on_front "$HOME_ID" >/dev/null
# Marketing CTAs read this. Configurable via WATCHLOG_PORTAL_URL; the sslip
# default is the DEMO environment only (production sets the env).
$W option update watchlog_portal_url "${WATCHLOG_PORTAL_URL:-https://watchlog.161.97.175.15.sslip.io}" >/dev/null
$W option update blogname "WatchLog" >/dev/null
$W option update blogdescription "The intelligence layer for the CCTV you already own." >/dev/null
$W option update timezone_string "Asia/Karachi" >/dev/null
$W rewrite structure '/%postname%/' --hard >/dev/null 2>&1 || true
$W rewrite flush --hard >/dev/null 2>&1 || true
say "settings applied"

say "done"
$W post list --post_type=page --fields=post_name,post_title,post_parent,post_status --format=table
