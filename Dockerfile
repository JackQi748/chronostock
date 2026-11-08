FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHRONOSTOCK_DB=/data/market.parquet \
    CHRONOSTOCK_REPORTS=/data/reports

WORKDIR /app
COPY pyproject.toml README.md ./
COPY chronostock ./chronostock
RUN python -m pip install --no-cache-dir . \
    && mkdir -p /data/reports

VOLUME ["/data"]
EXPOSE 8000

CMD ["chronostock", "serve", "--host", "0.0.0.0", "--port", "8000"]
