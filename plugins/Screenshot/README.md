# Screenshot

网页截图插件。当前版本：`1.2.0`

## 双接口策略

并行请求：

1. `screenshotsnap`（与台风插件同接口）
2. `microlink`

任一接口返回有效图片就立即发图，并取消另一个请求。

## 用法

```text
截图https://example.com
截图 https://example.com
```

引用含链接消息后发送：

```text
截图
```

## 配置

- `providers`：接口顺序/列表，默认 `["screenshotsnap", "microlink"]`
- `api_base`：screenshotsnap 地址
- `microlink_api`：microlink 地址
- `screenshot_width` / `screenshot_height`：抓取分辨率
- `max_dimension` / `max_file_size` / `jpeg_quality`：发送前 JPEG 压缩
- `retry_count` / `timeout`
- `notify_error`

## 说明

- 会识别 screenshotsnap 的 SVG 占位图并自动切到另一个接口
- 发送前转 JPEG，规避 869 缩略图失败
- 只有确认发送成功才提示“截图完成”
