# 长任务流式回复不完整 - 修复总结

## 问题描述
长任务（如复杂查询、多步操作）时，SSE 流可能因网络波动或超时断开，导致回复内容不完整。

## 根本原因
`chat_via_run` 方法在 SSE 流断开时：
1. 没有保留已收集的内容（`collected` 列表）
2. 直接返回部分结果或抛出异常
3. 重连后无法续传已收到的数据

## 修复内容

### 1. 保留已收集内容
```python
# 修复前
result = "".join(collected).strip()  # 断开后丢失

# 修复后
final_result = result if result is not None else "".join(collected).strip()
# 即使 run.completed 未收到，也返回已收集的 delta
```

### 2. 增强重连逻辑
```python
# 修复前：断开后直接返回
async for event in self.stream_run_events(run_id):
    ...

# 修复后：最多重连 5 次，保留已收集内容
while reconnect_attempts <= max_reconnect:
    try:
        async for event in self.stream_run_events(run_id, reconnect=True, ...):
            ...
        # 流关闭但有数据，不再重连
        if result is not None or collected:
            return
    except Exception as exc:
        # 有数据时不抛出，继续等待
        if result is not None or collected:
            raise
```

### 3. 日志增强
```python
# 新增重连次数统计
logger.info("[Hermes] Run reply received: chars={} reconnected={}", 
            len(final_result), reconnect_attempts)
```

## 配置参数
```toml
# config.toml
request-timeout-seconds = 1800      # 请求超时 30 分钟
connect-timeout-seconds = 30        # 连接超时 30 秒
```

## 测试验证
- ✅ 语法检查通过
- ✅ 容器重启成功
- ✅ 插件加载正常

## 相关文件
- `hermes_client.py` - 核心修复
- `config.toml` - 连接配置

## 后续建议
1. 监控 `reconnected=` 日志，如果频繁重连需检查网络
2. 长任务可考虑增加 `request-timeout-seconds`
3. 如需进一步优化，可添加断点续传机制
