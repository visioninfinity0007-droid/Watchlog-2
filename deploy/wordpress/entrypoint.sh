#!/usr/bin/env bash
#
# Sync the theme into the html volume, then hand over to WordPress.
#
# WHY THIS IS NEEDED: the official entrypoint only seeds /var/www/html
# from /usr/src/wordpress when the target is missing. Once a volume
# exists — which it does, because the site has data — a NEW or UPDATED
# theme baked into the image is silently ignored. Without this, every
# design change would need a manual docker cp, and the deploy would not
# actually deploy the design.
#
# Copies only the theme. Uploads, plugins and the database are the
# customer's and are never touched.
set -e

SRC=/usr/src/wordpress/wp-content/themes/watchlog
DST=/var/www/html/wp-content/themes/watchlog

if [ -d "$SRC" ]; then
  mkdir -p "$(dirname "$DST")"
  cp -a "$SRC/." "$DST/" 2>/dev/null || true
  chown -R www-data:www-data "$DST" 2>/dev/null || true
  echo "[watchlog] theme synced into the html volume"
fi

exec docker-entrypoint.sh "$@"
