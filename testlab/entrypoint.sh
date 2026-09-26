#!/bin/sh
set -eu

if [ "${REAL_RTSP:-1}" = "1" ] && [ "${RTSP_PORT:-554}" != "0" ]; then
  export MTX_RTSPADDRESS=":${RTSP_PORT:-554}"
  /usr/local/bin/mediamtx /app/mediamtx.yml &
fi

if [ "${LAB_GENERATE_VIDEO:-1}" = "1" ]; then
  ffmpeg -nostdin -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=640x360:rate=10" \
    -t 5 -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
    -movflags +faststart /tmp/watchlog-lab.mp4 || true
fi

exec python /app/virtual_device.py
