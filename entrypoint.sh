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

echo "启动XXXBot主应用..."
exec python3 ./main.py