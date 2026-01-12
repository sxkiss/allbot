#!/bin/bash
set -e
if ! python3 -c "import sys; assert sys.version_info >= (3,11,3)"; then
python -m pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple --upgrade pip
pip config set global.index-url https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
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
echo "启动py虚拟环境...请确保xbot配置文件已经填好，默认远程协议，远程redis"
#python3 -m venv venv
sleep 5
#echo "进入虚拟环境安装依赖..."
#source venv/bin/activate
cd /app
pip install -r requirements.txt
sleep 5

# 启动系统Redis服务（使用持久化目录）
echo "启动系统Redis服务..."
redis-server /etc/redis/redis.conf --daemonize yes --dir /data/redis

# 等待系统Redis服务启动
echo "等待系统Redis服务可用..."
sleep 2

echo "系统将只使用端口6379的Redis服务"

echo "启动XXXBot主应用..."
exec python3 ./main.py