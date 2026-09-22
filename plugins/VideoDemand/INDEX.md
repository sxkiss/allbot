<!-- AUTO-DOC: Update me when files in this folder change -->

# VideoDemand

视频点播插件：从多来源 API 获取视频，下载到本地临时目录后进行必要处理（可选 ffmpeg/ffprobe 抽帧/修复元数据），并以与 `VideoSender` 一致的参数格式发送视频消息。

## Files

| File | Role | Function |
|------|------|----------|
| main.py | Plugin | 视频菜单/随机/URL 点播处理与视频发送（缩略图抽帧、base64 编码、发送与清理） |
| config.toml | Config | 插件开关、命令、随机视频 URL、菜单图、缓存时间、ffmpeg/ffprobe 路径 |
| __init__.py | Entry | 插件导出 |
| temp/ | Runtime | 运行时临时目录（缓存/处理中间产物） |

