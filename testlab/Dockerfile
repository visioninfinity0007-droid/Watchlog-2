FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir pillow==11.3.0

WORKDIR /app
COPY virtual_device.py /app/virtual_device.py

EXPOSE 80 554 8000 37777 9001 3702/udp
ENTRYPOINT ["python", "/app/virtual_device.py"]
