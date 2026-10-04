FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POTTERY_DB=/data/pottery.db

WORKDIR /app

# 安装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app ./app

# 数据目录（SQLite 数据库与 WAL 文件，建议挂载为卷）
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 一键启动：uvicorn 启动时自动建表；库为空时写入演示数据
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
