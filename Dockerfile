FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/hongguo /opt/hongguo
COPY server.py accounts.py auth_routes.py overseas.py playback_jobs.py video_loader.py ./
COPY static ./static
COPY scripts ./scripts
RUN useradd --uid 10001 --create-home cinema && mkdir /data && chown cinema:cinema /data && find /opt/hongguo -name __pycache__ -type d -exec rm -rf {} +
ENV HONGGUO_SOURCE_DIR=/opt/hongguo HONGGUO_WEB_DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER cinema
EXPOSE 8787
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8787"]
