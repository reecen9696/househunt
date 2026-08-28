FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY passedin/ ./passedin/
COPY deploy/ ./deploy/
COPY config.yaml ./config.yaml

# /data is the mounted volume: SQLite store, page cache, logs, and the live
# config.yaml the settings panel writes to. bootstrap.py reconciles the
# volume's config with this image's on every boot.
EXPOSE 8080
CMD ["sh", "-c", "python deploy/bootstrap.py && exec python -m passedin --config /data/config.yaml serve"]
