#!/usr/bin/env bash
#
# WatchLog marketing site content.
#
# Idempotent: creates the pages the theme's slug-based templates render, sets
# the front page, and applies site options. Page bodies live in the templates
# under version control so public claims and merchant-facing policy text are
# reviewable in git.
#
set -eu
cd /var/www/html
W="wp --allow-root"

say() { echo "  $*" >&2; }

IDS=$($W post list --post_type=post --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true
IDS=$($W post list --post_type=page --title="Sample Page" --format=ids 2>/dev/null || true)
[ -n "$IDS" ] && $W post delete $IDS --force >/dev/null 2>&1 || true

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

HOME_ID=$(ensure_page home "Home")

# Product
ensure_page platform     "Platform"     >/dev/null
ensure_page incidents    "Incidents"    >/dev/null
ensure_page reporting    "Reporting"    >/dev/null
ensure_page site-health  "Site Health"  >/dev/null
ensure_page integrations "Integrations" >/dev/null

# Solutions
SOL_ID=$(ensure_page solutions "Solutions")
ensure_page quick-service-restaurants "Quick-Service Restaurants" "$SOL_ID" >/dev/null
ensure_page warehouses-logistics      "Warehouses & Logistics"     "$SOL_ID" >/dev/null
ensure_page retail                    "Retail"                     "$SOL_ID" >/dev/null
ensure_page manufacturing             "Manufacturing & Textiles"  "$SOL_ID" >/dev/null
ensure_page fuel-forecourt            "Fuel & Forecourt"           "$SOL_ID" >/dev/null
ensure_page schools-campuses          "Schools & Campuses"         "$SOL_ID" >/dev/null
ensure_page offices                   "Offices & Commercial"       "$SOL_ID" >/dev/null

# Trust, commercial and merchant-policy pages
ensure_page how-it-works "How it works" >/dev/null
ensure_page compatibility "Compatibility" >/dev/null
ensure_page security "Security" >/dev/null
ensure_page pricing "Pricing" >/dev/null
ensure_page setup "Setup" >/dev/null
ensure_page contact "Contact" >/dev/null
ensure_page faq "FAQ" >/dev/null
ensure_page privacy "Privacy" >/dev/null
ensure_page terms "Terms" >/dev/null
ensure_page refund-cancellation-service-delivery "Refund, Cancellation & Service Delivery" >/dev/null

$W option update show_on_front page >/dev/null
$W option update page_on_front "$HOME_ID" >/dev/null
$W option update watchlog_portal_url "${WATCHLOG_PORTAL_URL:-https://watchlog.161.97.175.15.sslip.io}" >/dev/null
$W option update blogname "WatchLog" >/dev/null
$W option update blogdescription "Video Analytics & CCTV Intelligence Platform" >/dev/null
$W option update timezone_string "Asia/Karachi" >/dev/null

# Merchant-facing business identity is intentionally configuration-driven.
# Never invent these values in source. When the owner supplies them, Coolify
# can set the environment variables and provisioning publishes them visibly.
[ -n "${WATCHLOG_LEGAL_NAME:-}" ] && $W option update watchlog_legal_name "$WATCHLOG_LEGAL_NAME" >/dev/null || true
[ -n "${WATCHLOG_BUSINESS_ADDRESS:-}" ] && $W option update watchlog_business_address "$WATCHLOG_BUSINESS_ADDRESS" >/dev/null || true
[ -n "${WATCHLOG_CONTACT_PHONE_DISPLAY:-}" ] && $W option update watchlog_contact_phone "$WATCHLOG_CONTACT_PHONE_DISPLAY" >/dev/null || true
[ -n "${WATCHLOG_BILLING_EMAIL:-}" ] && $W option update watchlog_billing_email "$WATCHLOG_BILLING_EMAIL" >/dev/null || true
[ -n "${WATCHLOG_CONTACT_EMAIL:-}" ] && $W option update watchlog_contact_email "$WATCHLOG_CONTACT_EMAIL" >/dev/null || true
[ -n "${WATCHLOG_WHATSAPP:-}" ] && $W option update watchlog_whatsapp "$WATCHLOG_WHATSAPP" >/dev/null || true

$W rewrite structure '/%postname%/' --hard >/dev/null 2>&1 || true
$W rewrite flush --hard >/dev/null 2>&1 || true
say "settings applied"

say "done"
$W post list --post_type=page --fields=post_name,post_title,post_parent,post_status --format=table
