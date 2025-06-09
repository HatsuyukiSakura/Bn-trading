# module_data_acquisition_websocket/Dockerfile
FROM python:3.10-slim-buster

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 定義啟動命令
# Cloud Run 服務預設監聽 8080 端口，但此服務通過 WebSocket 連線運行，
# 不需要外部 HTTP 請求，只需保持進程運行。
CMD ["python", "main.py"]

