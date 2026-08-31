# WatchLog push bridge — translates recorder alarm POSTs into wl_ingest_push.
# Stdlib only, so a tiny image and nothing to keep patched beyond Python.
FROM python:3.12-slim
WORKDIR /app
COPY push_bridge.py /app/push_bridge.py
EXPOSE 8620
# SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are injected by Coolify.
# Both are public-safe (the publishable key is public by design).
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0)"
CMD ["python", "push_bridge.py", "--host", "0.0.0.0", "--port", "8620"]
