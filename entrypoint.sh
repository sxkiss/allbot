#!/bin/bash
set -e
# @input: conf875/app.conf、环境变量 WX_TOKEN_KEY / WX_REDIS_LINK
# @output: 启动容器内 Redis + 875 协议服务（人脸验证由 875 二进制按配置拉起）
# @position: 875 纯协议服务容器入口（不含 AllBot 框架）

APP_DIR=/app
CONF_SRC="$APP_DIR/conf875/app.conf"
CONF_DST=/config/app.conf

echo "[entrypoint] 启动 Redis..."
redis-server /etc/redis/redis.conf --daemonize yes --dir /data/redis
for i in $(seq 1 20); do
    redis-cli ping >/dev/null 2>&1 && break
    sleep 0.5
done
redis-cli ping >/dev/null 2>&1 || { echo "[entrypoint] Redis 启动失败"; exit 1; }

if [ ! -x "$APP_DIR/wechatpad875" ]; then
    echo "[entrypoint] 缺少可执行文件 $APP_DIR/wechatpad875"
    exit 1
fi
if [ ! -f "$CONF_SRC" ]; then
    echo "[entrypoint] 缺少配置 $CONF_SRC"
    exit 1
fi

# 875 控制台静态资源自愈
if [ ! -d "$APP_DIR/swagger" ] && [ -d "$APP_DIR/swagger_seed" ]; then
    cp -a "$APP_DIR/swagger_seed" "$APP_DIR/swagger"
fi
if [ -d "$APP_DIR/swagger" ] && [ ! -e "$APP_DIR/swagger/swagger" ]; then
    ln -sfn . "$APP_DIR/swagger/swagger"
fi

# 部署配置到 875 强制读取路径
mkdir -p /config "$APP_DIR/conf"
cp "$CONF_SRC" "$CONF_DST"
cp "$CONF_SRC" "$APP_DIR/conf/app.conf"

WX_TOKEN_KEY_EFFECTIVE="${WX_TOKEN_KEY:-local-dev-placeholder}"
sed -i "s|^user_token_key = .*|user_token_key = \"${WX_TOKEN_KEY_EFFECTIVE}\"|" "$CONF_DST" "$APP_DIR/conf/app.conf"
if [ -n "${WX_REDIS_LINK:-}" ]; then
    sed -i "s|^redislink = .*|redislink = ${WX_REDIS_LINK}|" "$CONF_DST" "$APP_DIR/conf/app.conf"
fi
echo "[entrypoint] 875 卡密来源: ${WX_TOKEN_KEY:+环境变量}${WX_TOKEN_KEY:-兜底默认值}"

if [ -x "$APP_DIR/pad-face-verify-linux-x64" ]; then
    echo "[entrypoint] 人脸验证二进制就绪（由 875 按 pad_face_verifier_enabled 拉起，端口 18080）"
else
    echo "[entrypoint] 警告：缺少 pad-face-verify-linux-x64，人脸验证不可用"
fi

export BEEGO_CONFIG_PATH="$CONF_DST"
export PAD_FACE_VERIFY_BIN="$APP_DIR/pad-face-verify-linux-x64"
cd "$APP_DIR"
echo "[entrypoint] 启动 875 协议服务（HTTP 18062 / WS 18089 / 人脸 18080）..."
exec ./wechatpad875
