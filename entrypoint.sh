#!/bin/bash
set -e
# @input: /app/main_config.toml, /app/requirements.txt, /etc/redis/redis.conf
# @output: 每次启动安装 Python 依赖，启动容器内 Redis，并运行 /app/main.py
# @position: Docker 容器入口脚本（安装依赖 -> 启动 Redis -> 启动主程序）
# @auto-doc: Update header and related docs when startup flow changes

# 配置 pip 镜像源（必须在所有 pip 操作之前）
pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
pip config set global.trusted-host mirrors.tuna.tsinghua.edu.cn

if ! python3 -c "import sys; assert sys.version_info >= (3,11,3)"; then
    python -m pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --upgrade pip
    wget https://www.python.org/ftp/python/3.11.3/Python-3.11.3.tgz
    tar xzf Python-3.11.3.tgz
    cd Python-3.11.3
    apt-get install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev
    ./configure --enable-optimizations --enable-loadable-sqlite-extensions
    make -j $(nproc)
    make install
    python3.11 --version
fi
cd ..
echo "启动 Python 运行环境...请确保 /app/main_config.toml 已按当前部署环境填写"
cd /app
echo "安装 Python 依赖..."
pip install -r requirements.txt
pip install --upgrade pip
echo "Python 依赖安装完成"

# 启动系统Redis服务（使用持久化目录）
echo "启动系统Redis服务..."
redis-server /etc/redis/redis.conf --daemonize yes --dir /data/redis

# 等待系统Redis服务启动
echo "等待系统Redis服务可用..."
sleep 2

# 启动 875 协议服务（WeChatPadProMAX，监听 0.0.0.0:18062，WS 18089）
# 注意：875 是 Beego 应用，固定读取 /app/conf/app.conf（不是 conf875/），
# 必须先部署配置到该路径，否则 user_token_key 等配置读不到会导致启动失败。
if [ -x /app/wechatpad875 ] && [ -f /app/conf875/app.conf ]; then
    echo "部署 875 配置到 /config/app.conf（入口强制路径）..."
    mkdir -p /config
    cp /app/conf875/app.conf /config/app.conf
    # 兼容 Beego 默认路径（部分版本读 /app/conf/app.conf）
    mkdir -p /app/conf
    cp /app/conf875/app.conf /app/conf/app.conf
    # 卡密：优先用环境变量 WX_TOKEN_KEY，未设置则兜底默认值，
    # 保证容器在任何情况下都能启动（授权是否有效由控制面 authserver 裁决）
    WX_TOKEN_KEY_EFFECTIVE="${WX_TOKEN_KEY:-local-dev-placeholder}"
    sed -i "s|^user_token_key = .*|user_token_key = \"${WX_TOKEN_KEY_EFFECTIVE}\"|" /config/app.conf /app/conf/app.conf
    echo "875 卡密已设置（来源: ${WX_TOKEN_KEY:+环境变量}${WX_TOKEN_KEY:-兜底默认值}）"
    # redis 地址从环境变量注入（容器内 redis 默认 127.0.0.1:6379）
    if [ -n "${WX_REDIS_LINK:-}" ]; then
        sed -i "s|^redislink = .*|redislink = ${WX_REDIS_LINK}|" /config/app.conf /app/conf/app.conf
    fi
    echo "启动 875 协议服务..."
    # 强制指定 Beego 配置文件路径，确保主程序读取正确的端口和人脸验证配置
    export BEEGO_CONFIG_PATH=/config/app.conf
    cd /app && nohup ./wechatpad875 > /tmp/875.log 2>&1 &
    sleep 3
    cd /app
fi


echo "启动XXXBot主应用..."
exec python3 ./main.py
