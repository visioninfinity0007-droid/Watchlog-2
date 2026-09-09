# WatchLog portal - static export served by nginx.
#
# There is no Node server in production. The app talks to Supabase from
# the browser, and every table is behind RLS scoped to the signed-in
# user's tenant, so authorisation is enforced by Postgres rather than by
# a server we would have to run, secure and patch.
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Every NEXT_PUBLIC_* is inlined by Next at build time, so ALL public config the
# app reads must be an ARG->ENV here or it bakes empty regardless of runtime env.
# The publishable key is public by design. The remaining four are the branded
# URLs a domain cutover changes (marketing/installer/billing); wiring them here is
# what makes a Coolify build-arg switch actually reach the static bundle.
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_MARKETING_URL
ARG NEXT_PUBLIC_INSTALLER_URL
ARG NEXT_PUBLIC_BILLING_URL
ARG NEXT_PUBLIC_BILLING_PROVIDER
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_MARKETING_URL=$NEXT_PUBLIC_MARKETING_URL
ENV NEXT_PUBLIC_INSTALLER_URL=$NEXT_PUBLIC_INSTALLER_URL
ENV NEXT_PUBLIC_BILLING_URL=$NEXT_PUBLIC_BILLING_URL
ENV NEXT_PUBLIC_BILLING_PROVIDER=$NEXT_PUBLIC_BILLING_PROVIDER
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/out /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
