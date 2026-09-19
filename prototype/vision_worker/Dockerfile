FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "requests>=2.32" "boto3>=1.35"
COPY worker.py /app/worker.py
ENV PORT=8640 PYTHONUNBUFFERED=1
EXPOSE 8640
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8640/health',timeout=4).status==200 else 1)"
CMD ["python","/app/worker.py"]
