FROM python:3.12-slim

RUN groupadd -r atronixx && useradd -r -g atronixx -d /app -s /usr/sbin/nologin atronixx \
    && apt-get update && apt-get install -y --no-install-recommends tini curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .
RUN mkdir -p /data && chown -R atronixx:atronixx /app /data

USER atronixx
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import time,sys; d=open('/tmp/atronixx.hb').read(); sys.exit(0 if time.time()-float(d)<90 else 1)" || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python3", "main.py"]
