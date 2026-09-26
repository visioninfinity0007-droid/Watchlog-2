FROM bluenviron/mediamtx:1-ffmpeg AS mediamtx

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REAL_RTSP=1 \
    LAB_GENERATE_VIDEO=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir pillow==11.3.0

COPY --from=mediamtx /mediamtx /usr/local/bin/mediamtx

WORKDIR /app
COPY virtual_device.py /app/virtual_device.py
COPY mediamtx.yml /app/mediamtx.yml
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 80 554 8000 37777 9001 3702/udp
ENTRYPOINT ["/app/entrypoint.sh"]
