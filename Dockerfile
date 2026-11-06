FROM python:3.12-slim

ENV TZ=Asia/Shanghai
WORKDIR /app

RUN sed -i 's@deb.debian.org@repo.huaweicloud.com@g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        redis-server curl ca-certificates tzdata libc6 libstdc++6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /data/redis && chmod 777 /data/redis

COPY redis.conf /etc/redis/redis.conf

# 875 协议服务二进制与运行资源
COPY wechatpad875 /app/wechatpad875
COPY conf875 /app/conf875
COPY wxserver_runtime/assets /app/assets
COPY wxserver_runtime/static /app/static
COPY wxserver_runtime/swagger /app/swagger_seed
COPY wxserver_runtime/bin/pad-face-verify-linux-x64 /app/pad-face-verify-linux-x64

# 授权/运行库与运行时数据资源
COPY lib /app/lib
COPY lib875 /app/lib875
COPY 08sae.dat /app/08sae.dat
COPY rqtx.dat /app/rqtx.dat
COPY version.json /app/version.json

RUN chmod +x /app/wechatpad875 /app/pad-face-verify-linux-x64

ENV PAD_FACE_VERIFY_BIN=/app/pad-face-verify-linux-x64
ENV WX_HOST=0.0.0.0
ENV WX_PORT=18062
ENV WX_REDIS_LINK=127.0.0.1:6379

# 875 实例租约/授权（控制面校验，必需）
ENV WECHATPAD_DEV_ADMIN_API=1
ENV WECHATPAD_DEV_ALLOW_PERSISTED_AUTH=1
ENV ADMIN_API_BASE=https://v.sxkiss.top

EXPOSE 18062 18089 18080
VOLUME ["/data/redis"]

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
