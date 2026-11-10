# MessageParserRecorder — 框架通用消息解析记录插件

## 定位
**框架层通用插件**（继承 `PluginBase` + 标准 `on_*_message` 装饰器），不是某个协议适配器专属逻辑。
框架 `allbot_legacy` 已经负责各类型消息的解析与资源下载（调用 `self.bot.get_msg_image` /
`download_voice` / `download_video` / `download_attach`，875 适配器已把这些方法适配到协议）。
本插件只读取框架处理好的 `message` 字段做**结构化解析 + 记录**：

- **不发送**：不调用任何 `bot.send_*`，不产生任何回复
- **不主动下载**：不调用任何下载接口；仅对框架已落盘的资源做只读 `stat` 校验
- **只记录**：结构化 JSONL 写到 `records/messages.jsonl`，另在日志打印一条摘要

## 覆盖类型与解析来源

| 类型 | 装饰器 | 读取的框架字段 / 解析内容 |
|---|---|---|
| 文本 | `on_text_message` | `Content` |
| @ | `on_at_message` | `Content` + `Ats` |
| 图片 | `on_image_message` | `ImagePath`(stat) / `ImageMD5` / `ImageBase64` / XML `aeskey,cdnmidimgurl` |
| 语音 | `on_voice_message` | `VoicePath` / voicemsg XML 属性 |
| 视频 | `on_video_message` | `Video`(框架下载结果) / XML `cdnvideourl,aeskey` |
| 文件 | `on_file_message` | `File` / `Filename` / `FileExtend` / `appattach{attachid,totallen,fileext}` |
| 表情 | `on_emoji_message` | `emoji` XML `md5,len` |
| 引用消息 | `on_quote_message` | `Quote{MsgType,Nickname,Content,NewMsgId...}` |
| 引用资源 | 同上 `quoted_resource` | `aeskey/cdnmidimgurl/cdnurl/cdnthumbaeskey/ImageMD5` + 被引用 XML 二次解析 |
| 卡片/名片/文章/链接/小程序/位置/转账 | `on_xml_message` / `on_article_message` | `<appmsg>` 通用解析，`type_name` 判定；文章多图文 `mmreader/item` 逐条 |
| 拍一拍 | `on_pat_message` | `Patter/Patted/PatSuffix` + `<sysmsg type=pat>` 兜底 |
| 系统 | `on_system_message` | `<sysmsg>` 解析 |
| 其它 | `on_other_message` | 全字段键名 + 内容预览 |

资源"可解析下载"体现在：把框架已下载/落盘的路径、大小、以及仍可据此下载的 CDN 定位符
（aeskey/cdnmidimgurl/attachid 等）全部结构化记录，供后续下载管线或排障使用；插件自身不重复下载。

## 配置
`config.toml` → `[MessageParserRecorder]`：`enable` / `record_file` / `log_summary` /
`max_text_len`（大 base64 截断）/ `verify_resource_path`（只读 stat）。

## 启用
框架按插件目录自动加载；确保 `main_config.toml` 的 `disabled-plugins` 不含 `MessageParserRecorder`，
且 `config.toml` 里 `enable = true`。重启/重载插件后生效。