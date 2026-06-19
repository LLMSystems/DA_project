# 試題一：門牌爬蟲容器（headless Chromium + ddddocr 自動驗證碼）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER=/usr/bin/chromedriver

# Chromium 與 driver（Debian 套件），以及 ddddocr/opencv 執行所需的最小系統庫。
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        libgl1 \
        libglib2.0-0 \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 預設：以 auto 驗證碼、headless 抓台北市各區，DB 與 Log 寫入共用 volume。
# 可於 docker compose / docker run 覆寫 command 調整參數（例如限定 --areas 加速 demo）。
CMD ["python", "main.py", \
     "--captcha", "auto", "--headless", \
     "--db-path", "/data/doorplate.sqlite3", \
     "--log-path", "/logs/crawler/crawler.log"]
