FROM python:3.11-slim

WORKDIR /app/python-service

# 系统依赖（curl 用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 环境变量
ENV DB_DIR=/data/hithink-finance \
    PORT=50052 \
    LOG_LEVEL=info \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

EXPOSE 50052

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=30s \
    CMD curl -f http://localhost:50052/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "50052"]