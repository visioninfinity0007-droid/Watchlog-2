# WatchLog billing service (provider boundary: hosted checkout + signed webhook).
# Deployed on Coolify from /prototype/billing.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.1"
COPY serve.py ./
ENV PORT=8640
EXPOSE 8640
CMD ["python", "serve.py"]
