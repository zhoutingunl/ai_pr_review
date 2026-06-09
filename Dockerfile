# 仅打包本应用；Hermes 网关是外部依赖，通过 HERMES_BASE 指向（见 docs/public.md）
FROM python:3.11-slim

WORKDIR /app

# git 供 config.py 回落 `gh auth token` 之外的场景及依赖构建
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 数据目录（SQLite，建议用 volume 持久化）
RUN mkdir -p data
EXPOSE 38001

CMD ["python3.11", "app.py"]
