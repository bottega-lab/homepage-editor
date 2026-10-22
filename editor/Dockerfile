# Homepage Editor v1.0.0. SPDX-License-Identifier: MIT
# Built for linux/amd64 and linux/arm64 (Raspberry Pi 3/4/5 with a 64-bit system, other ARM boards).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PUID=1000 PGID=1000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py start.py ./
COPY static ./static
EXPOSE 8080
# Healthy when the web server answers (the tab icon needs no login).
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/favicon.svg', timeout=4)" || exit 1
# start.py fixes the owner of /backups and /data, then runs the editor as PUID:PGID.
CMD ["python", "/app/start.py"]
