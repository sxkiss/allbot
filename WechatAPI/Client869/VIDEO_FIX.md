# 视频下载不完整问题 - 修复总结

## 问题现象
引用视频消息下载后只有 60KB，而实际视频应有 2.5MB。所有视频缓存文件都是 60KB 截断状态。

## 根本原因
1. **协议服务器限制**：`/api/Tools/DownloadVideo` 和 `/message/GetMsgVideo` 接口只返回视频的前 60KB
2. **缓存命中问题**：旧的 60KB 截断文件被缓存，后续请求直接返回缓存，不再尝试重新下载
3. **分段下载未触发**：虽然有 `_download_video_segments` 方法，但因为缓存命中而从未执行

## 修复内容

### 1. 检测不完整缓存
```python
# 修复前：直接返回缓存
if cached:
    return cached

# 修复后：检查缓存大小，小于 100KB 认为可能不完整
if cached:
    cached_len = len(cached)
    if cached_len < 100 * 1024:  # 小于 100KB
        logger.warning("缓存可能不完整，尝试重新下载")
        # 清除不完整缓存
        self._media_cache_mem.pop(cache_key, None)
        # 删除缓存文件
        os.remove(cache_file)
    else:
        return cached
```

### 2. 触发分段下载
清除不完整缓存后，代码会执行完整的下载流程：
1. 尝试 `/api/Tools/DownloadVideo`（仍返回 60KB）
2. 检测到 `totalLen > data_len`，触发 `_download_video_segments`
3. 使用 `/message/GetMsgVideo` 分段下载完整视频

## 已知限制

### 协议服务器限制
微信协议服务器的视频下载接口可能存在以下限制：
1. **单次下载大小限制**：可能只允许下载前 N KB
2. **分段参数不支持**：`Section` 参数可能被忽略
3. **需要特殊权限**：分段下载可能需要额外的认证或权限

### 测试建议
如果修复后仍然只有 60KB，说明协议服务器不支持分段下载，需要：
1. 升级协议服务器版本
2. 联系协议服务器开发者添加分段下载支持
3. 或使用其他方法获取完整视频（如 CDN URL 直接下载）

## 相关文件
- `WechatAPI/Client869/client.py` - 视频下载逻辑
- `plugins/HermesPlugin/media_pipeline.py` - 引用媒体处理

## 日志关键字
- `[Client869] download_video 缓存可能不完整` - 触发重新下载
- `[Client869] _download_video_segments` - 分段下载开始
- `[Client869] download_video 分段下载成功` - 分段下载完成
