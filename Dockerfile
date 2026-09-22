FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV TZ=Asia/Shanghai
ENV IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg

# 更新软件源
RUN sed -i 's@deb.debian.org@repo.huaweicloud.com@g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖，减少镜像层数
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    redis-server \
    p7zip-full \
    curl \
    && if ! apt-get install -y --no-install-recommends unrar-free; then \
        apt-get install -y --no-install-recommends unrar || echo "无法安装unrar-free，继续安装"; \
    fi \
    && if ! ln -sf /usr/bin/7za /usr/bin/7z; then \
        echo "无法创建7z链接，但继续执行"; \
    fi \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 创建Redis数据持久化目录
RUN mkdir -p /data/redis && chmod 777 /data/redis

# 复制 Redis 配置
COPY redis.conf /etc/redis/redis.conf

# 复制应用代码
COPY . .

# 设置权限
RUN chmod -R 755 /app \
    && find /app -name "XYWechatPad" -exec chmod +x {} \; \
    && find /app -type f -name "*.py" -exec chmod +x {} \; \
    && find /app -type f -name "*.sh" -exec chmod +x {} \;

# 创建日志目录
RUN mkdir -p /app/logs && chmod 777 /app/logs

# 启动脚本
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# 暴露端口（仅后台管理端口）
EXPOSE 9090

# 数据卷（Redis持久化）
VOLUME ["/data/redis"]

CMD ["./entrypoint.sh"]
