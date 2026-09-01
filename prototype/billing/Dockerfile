# WatchLog billing service (provider boundary: hosted checkout + signed webhook).
# Deployed on Coolify from /prototype/billing.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.1"
COPY serve.py ./
ENV PORT=8640
EXPOSE 8640
# python-based healthcheck (slim image has no curl/wget)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8640/',timeout=4).status==200 else 1)"
CMD ["python", "serve.py"]
