
# 使用 Python 3.10 作為基礎映像
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 複製所有檔案進入容器
COPY . .

# 安裝相依套件
RUN pip install --no-cache-dir -r requirements.txt

# 開放 Cloud Run 預設 port
ENV PORT 8080

# 啟動主程式
CMD ["python", "main.py"]
