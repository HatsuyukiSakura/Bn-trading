# 從 repo 根目錄建置：docker build -f ema_sma_bot/Dockerfile -t ema-bot .
FROM python:3.11-slim

WORKDIR /data
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app DB_PATH=/data/state.db

COPY ema_sma_bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ema_sma_bot /app/ema_sma_bot

VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "ema_sma_bot"]
CMD ["live"]
