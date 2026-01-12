FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV TZ=Asia/Shanghai
ENV IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg

# 更新软件源
RUN sed -i 's@deb.debian.org@repo.huaweicloud.com@g' /etc/apt/sources.list.d/debian.sources
RUN apt-get update

# 分步安装系统依赖，以便于调试
RUN apt-get install -y ffmpeg
RUN apt-get install -y redis-server
RUN apt-get install -y build-essential python3-dev
RUN apt-get install -y p7zip-full
RUN apt-get install -y unrar-free || apt-get install -y unrar || echo "无法安装unrar-free，继续安装"
RUN apt-get install -y curl netcat-openbsd || apt-get install -y curl netcat-traditional || apt-get install -y curl netcat
RUN ln -sf /usr/bin/7za /usr/bin/7z || echo "无法创建7z链接，但继续执行"

# 安装 procps 工具
RUN apt-get install -y procps

# 清理apt缓存减小镜像大小
RUN apt-get clean && rm -rf /var/lib/apt/lists/*

# 创建Redis数据持久化目录
RUN mkdir -p /data/redis && chmod 777 /data/redis

# 复制 Redis 配置
COPY redis.conf /etc/redis/redis.conf

# 复制依赖文件
COPY requirements.txt .

# 升级pip并安装Python依赖
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir websockets "httpx[http2]"

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
