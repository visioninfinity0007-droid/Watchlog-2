# WatchLog viewer — static page, served by nginx.
#
# The viewer is a single HTML file with no build step and no backend. It
# talks directly to Supabase from the browser.
#
# No credentials are baked into the image. The page asks for the project
# URL and publishable key on first load and keeps them in the browser's
# localStorage, so a stranger who finds the URL sees a config form and
# nothing else. Do not add keys here.

FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
