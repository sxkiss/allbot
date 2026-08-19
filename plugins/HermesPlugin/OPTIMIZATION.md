# HermesPlugin HTTP 优化

## 优化内容

### 1. 连接池 (TCPConnector)
- **总连接池大小**: 20 个连接
- **单主机限制**: 10 个连接
- **DNS 缓存**: 5 分钟
- **连接复用**: 启用 keep-alive
- **自动清理**: 启用 cleanup_closed

**优势**:
- 减少 TCP 握手开销
- 支持并发请求
- 避免连接耗尽

### 2. 自动重连 (指数退避)
```python
# 重试配置
max_retries = 3
retry_delay = 1.0  # 初始延迟
# 退避序列: 1s -> 2s -> 4s
```

**重试场景**:
- 连接超时 (ServerTimeoutError)
- 网络错误 (ClientError)
- 健康检查失败

**不重试场景**:
- 非重试异常（如 401、404）
- 业务逻辑错误

### 3. 连接健康监控
```python
# 健康检查间隔: 30 秒
health_check_interval = 30.0

# 连续错误阈值: 3 次
if consecutive_errors >= 3:
    connected = False
```

**状态跟踪**:
- `is_connected`: 连接状态
- `_last_health_check`: 上次健康检查时间
- `_consecutive_errors`: 连续错误计数
- `_last_error_at`: 上次错误时间

**自动恢复**:
- 健康检查通过 → 重置错误计数
- 连续错误 ≥3 → 标记断开
- 下次请求时重新检测

### 4. 操作重试机制
```python
async def _execute_with_retry(operation_name, operation, *args, **kwargs):
    # 指数退避重试
    for attempt in range(max_retries):
        try:
            return await operation(*args, **kwargs)
        except (ServerTimeoutError, ClientError) as exc:
            # 重试
            await asyncio.sleep(retry_delay * 2^attempt)
        except Exception:
            # 不重试，直接抛出
            raise
```

## 配置参数

### config.toml
```toml
[Hermes]
api-base-url = "http://l.sxkiss.top:8642"
api-key = "e8745b717a6a883fa15e8392b0a7093219050da61c2a94faf2c35d8ddf822e44"

# 超时配置
request-timeout-seconds = 1800      # 请求超时 30 分钟
connect-timeout-seconds = 30        # 连接超时 30 秒

# 重试配置
max-retries = 3                     # 最大重试次数
retry-delay-seconds = 1.0           # 初始重试延迟
health-check-interval-seconds = 30.0 # 健康检查间隔
```

## 性能指标

### 连接池
- **容量**: 20 并发连接
- **复用**: TCP keep-alive
- **DNS 缓存**: 5 分钟 TTL

### 重试策略
- **最大延迟**: 4 秒（1s → 2s → 4s）
- **总超时**: 连接 30s + 请求 1800s
- **失败策略**: 不重试业务错误

### 健康检查
- **频率**: 30 秒/次
- **超时**: 5 秒
- **状态**: 连续 3 次错误视为断开

## 故障处理

### 连接失败
1. 尝试连接（3 次，指数退避）
2. 验证健康检查
3. 失败则抛出异常

### 请求超时
1. 记录错误
2. 等待 1s/2s/4s 后重试
3. 失败则返回错误

### 服务恢复
1. 下次请求时触发健康检查
2. 通过则重置状态
3. 未通过则标记断开

## 测试验证

```bash
# 健康检查
curl http://l.sxkiss.top:8642/health

# 模型列表
curl -H "Authorization: Bearer *** http://l.sxkiss.top:8642/v1/models

# 聊天测试
curl -X POST http://l.sxkiss.top:8642/v1/chat/completions \
  -H "Authorization: Bearer *** \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}'
```

## 兼容性

- ✅ 向后兼容原有配置
- ✅ 无需修改插件调用代码
- ✅ 支持现有所有 API 端点
- ✅ 自动适配新参数

## 相关文件

- `hermes_client.py` - 客户端核心实现
- `config.toml` - 配置文件
- `OPTIMIZATION.md` - 本文档
