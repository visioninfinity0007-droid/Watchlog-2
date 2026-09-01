# WatchLog report-runner service (the scheduled daily-report trigger).
# Deployed on Coolify from /prototype/reporter; n8n calls POST /run/<token>.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.1" "requests>=2.31"
COPY daily_report.py email_template.py serve.py ./
ENV PORT=8630
EXPOSE 8630
# python-based healthcheck (slim image has no curl/wget)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8630/',timeout=4).status==200 else 1)"
# Dry-run by default: composes + records what it would send, messages no one,
# until REPORT_SEND=true is set on the deployed service (CLIENT-BLOCKED step).
CMD ["python", "serve.py"]
