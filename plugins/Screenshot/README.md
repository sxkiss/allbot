# Screenshot

网页截图插件，调用与台风插件相同的 [screenshotsnap](https://screenshotsnap.com/api/screenshot) 接口生成截图并发送图片。

## 用法

```text
截图https://example.com
截图 https://example.com
```

引用一条包含链接的消息，再发送：

```text
截图
```

## 配置

`config.toml`：

- `enable`：开关
- `commands`：触发词，默认 `["截图"]`
- `api_base`：截图 API，默认 `https://screenshotsnap.com/api/screenshot`
- `screenshot_width` / `screenshot_height`：分辨率
- `retry_count` / `timeout`：重试与超时
