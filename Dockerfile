FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/hongguo /opt/hongguo
COPY server.py accounts.py auth_routes.py overseas.py playback_jobs.py video_loader.py country_access.py ./
COPY static ./static
COPY scripts ./scripts
ARG GEOIP_MONTH
RUN python scripts/update_geoip.py --destination /opt/geoip/country.mmdb --month "${GEOIP_MONTH}"
# The installer clones with umask 077. COPY preserves those root-only modes;
# normalize public runtime files inside the image, never host secrets or /data.
RUN chmod -R a+rX /app /opt/hongguo /opt/geoip \
    && useradd --uid 10001 --create-home cinema \
    && mkdir /data && chown cinema:cinema /data \
    && find /opt/hongguo -name __pycache__ -type d -exec rm -rf {} +
ENV HONGGUO_SOURCE_DIR=/opt/hongguo HONGGUO_WEB_DATA_DIR=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV TACO_GEOIP_DATABASE=/opt/geoip/country.mmdb
USER cinema
# Fail the build if the actual runtime user cannot import the app or read GeoIP.
# Importing does not run the lifespan or create accounts / setup tokens.
RUN python -c "import os, server; from country_access import CountryAccess; guard = CountryAccess(os.environ['TACO_GEOIP_DATABASE']); assert guard.status('223.5.5.5') == 403, 'GeoIP database unreadable'; guard.close()"
EXPOSE 8787
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8787"]
