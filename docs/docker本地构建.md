# AllBot Docker 部署指南

本指南说明 AllBot 的两种 Docker 部署方式：官方镜像部署与本地构建部署。

## 1. 前置条件

- 已安装 Docker
- 已安装 Docker Compose
- 已获取项目代码

## 2. 部署方式选择

### 2.1 进入项目根目录

```bash
cd /path/to/allbot
```

### 2.2 方式一：使用官方镜像（推荐）

适用场景：快速部署，无需本地构建

```bash
# 使用官方镜像启动服务
docker-compose up -d
```

这会拉取 sxkiss/allbot:latest 镜像并启动服务。

更新到最新版本：

```bash
docker-compose pull
docker-compose up -d
```

### 2.3 方式二：本地构建镜像

适用场景：需要自定义代码或调试

```bash
# 使用本地构建配置启动服务
docker-compose -f docker-compose.local.yml up -d --build
```

或分步构建：

```bash
# 构建本地镜像
docker build -t sxkiss/allbot:local .

# 使用本地镜像启动服务
docker-compose -f docker-compose.local.yml up -d
```

## 3. docker-compose 结构说明

### 3.1 官方镜像配置（docker-compose.yml）

- 容器名：allbot
- 管理后台端口：9090
- 镜像：sxkiss/allbot:latest
- Redis 数据卷：redis_data:/data/redis

### 3.2 本地构建配置（docker-compose.local.yml）

- 容器名：allbot
- 管理后台端口：9090
- 镜像：sxkiss/allbot:local
- 代码挂载：./:/app 便于开发调试
- Redis 数据卷：redis_data:/data/redis

## 4. 常见问题

### 4.1 entrypoint.sh 无执行权限

解决方法：执行 chmod +x entrypoint.sh。

### 4.2 Redis 连接失败

确保 Redis 服务正常启动，或检查主机端口冲突。

### 4.3 文件无法写入

可临时放宽权限：chmod -R 755 .

## 5. 生产环境建议

### 5.1 使用官方镜像部署

- 不要挂载代码目录（./:/app）
- 固定镜像版本号，避免自动升级
- 只挂载必要的数据目录

### 5.2 使用本地构建部署

- 构建完成后移除代码挂载
- 使用版本标签管理镜像
- 定期更新镜像和依赖

## 6. 镜像管理

### 6.1 构建新版本镜像

docker build -t sxkiss/allbot:latest .
docker build -t sxkiss/allbot:v1.0.0 .

### 6.2 推送镜像到仓库

docker login
docker push sxkiss/allbot:latest
docker push sxkiss/allbot:v1.0.0

### 6.3 清理旧镜像

docker images | grep sxkiss/allbot
docker rmi <image-id>
