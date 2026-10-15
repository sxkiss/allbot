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

# wxserver（由 build.sh 构建并复制到此目录）
COPY wxserver /app/wxserver
RUN chmod +x /app/wxserver

# wxserver 运行时资源（sae.dat / setting.json / templates）
COPY wxserver_runtime/assets /app/assets
COPY wxserver_runtime/static /app/static

# 875 API 控制台静态资源（Swagger UI + swagger.json，313 接口）
# 875 二进制把 StaticDir["/"] 指向相对目录 swagger，即 /app/swagger；
# 缺少该目录时根路径与 /swagger 全部 404，故随镜像分发一份种子副本，
# 由 entrypoint 在 /app/swagger 缺失时自愈还原（防挂载覆盖或误删）。
COPY wxserver_runtime/swagger /app/swagger_seed

# 875 人脸验证二进制（41MB，静态链接，随镜像分发）
COPY wxserver_runtime/bin/pad-face-verify-linux-x64 /app/pad-face-verify-linux-x64
RUN chmod +x /app/pad-face-verify-linux-x64
ENV PAD_FACE_VERIFY_BIN=/app/pad-face-verify-linux-x64

# 运行时配置（内置默认，compose 无需重复声明）
ENV WX_HOST=127.0.0.1
ENV WX_PORT=18062
ENV WX_REDIS_LINK=127.0.0.1:6379
ENV WX_AUTH_SERVER=https://v.sxkiss.top
ENV MYSQL_HOST=mysql
ENV MYSQL_PORT=3306
ENV MYSQL_USER=weixin
ENV MYSQL_PASSWORD=wxserver_2026
ENV MYSQL_DATABASE=weixin
ENV MYSQL_PARAMS=charset=utf8mb4&parseTime=true&loc=Local
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379
ENV REDIS_DB=2
# 875 门禁校验（走公网控制面）
ENV WECHATPAD_DEV_ADMIN_API=1
ENV ADMIN_API_BASE=https://v.sxkiss.top
ENV WECHATPAD_DEV_ALLOW_PERSISTED_AUTH=1

# 暴露端口（后台 9090 / 875 HTTP 18062 / 875 WS 18089 / 人脸验证 18080）
EXPOSE 9090 18062 18089 18080

# 数据卷（Redis持久化）
VOLUME ["/data/redis"]

CMD ["./entrypoint.sh"]
