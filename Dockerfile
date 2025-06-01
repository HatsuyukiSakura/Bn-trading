# Dockerfile for Crypto Bot
FROM python:3.10-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "main:app"]
