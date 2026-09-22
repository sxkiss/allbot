import os
import base64
import xml.etree.ElementTree as ET
import tomllib
import aiohttp
from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_xml_message, on_text_message
from utils.plugin_base import PluginBase


class FileDownloader(PluginBase):
    description = "文件下载插件"
    author = "XXXBot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        # 获取配置文件路径
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")

        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)

            # 读取基本配置
            basic_config = config.get("basic", {})
            self.enable = basic_config.get("enable", False)  # 读取插件开关
            self.auto_download = basic_config.get("auto_download", True)  # 是否自动下载文件
        except Exception as e:
            logger.error(f"加载FileDownloader配置文件失败: {str(e)}")
            self.enable = False  # 如果加载失败，禁用插件
            self.auto_download = True

        # 创建下载目录
        self.download_dir = os.path.join(os.path.dirname(__file__), "downloads")
        os.makedirs(self.download_dir, exist_ok=True)

        logger.info(f"FileDownloader插件初始化完成，自动下载: {self.auto_download}")

    @on_text_message(priority=50)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息，检测下载命令"""
        if not self.enable:
            return True

        content = message.get("Content", "")

        # 检测是否是下载文件命令
        if content == "下载文件":
            logger.info("FileDownloader: 收到下载文件命令")
            await bot.send_text_message(
                message["FromWxid"],
                "FileDownloader: 请发送文件消息进行下载"
            )
            return False  # 阻止后续插件处理

        return True  # 允许后续插件处理

    @on_xml_message(priority=99)  # 使用高优先级确保先处理
    async def handle_xml(self, bot: WechatAPIClient, message: dict):
        """处理XML消息，检测文件消息"""
        if not self.enable:
            return True

        logger.info(f"FileDownloader: 收到XML消息，消息ID: {message.get('MsgId', '')}")

        try:
            # 解析XML内容
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            if appmsg is None:
                return True

            type_element = appmsg.find("type")
            if type_element is None:
                return True

            type_value = int(type_element.text)
            logger.info(f"FileDownloader: XML消息类型: {type_value}")

            # 检测是否是文件消息（类型6）
            if type_value == 6:
                logger.info("FileDownloader: 检测到文件消息")

                # 提取文件信息
                title = appmsg.find("title").text
                appattach = appmsg.find("appattach")
                attach_id = appattach.find("attachid").text
                file_extend = appattach.find("fileext").text
                total_len = int(appattach.find("totallen").text)

                logger.info(f"FileDownloader: 文件名: {title}")
                logger.info(f"FileDownloader: 文件扩展名: {file_extend}")
                logger.info(f"FileDownloader: 附件ID: {attach_id}")
                logger.info(f"FileDownloader: 文件大小: {total_len}")

                # 检查是否自动下载
                if self.auto_download:
                    # 发送通知
                    await bot.send_text_message(
                        message["FromWxid"],
                        f"FileDownloader: 正在下载文件...\n文件名: {title}.{file_extend}"
                    )

                    # 使用 /Tools/DownloadFile API 下载文件
                    logger.info("FileDownloader: 开始下载文件...")

                    try:
                        # 分段下载大文件
                        # 每次下载 64KB
                        chunk_size = 64 * 1024  # 64KB
                        app_id = appmsg.get("appid", "")

                        # 创建一个字节数组来存储完整的文件数据
                        file_data = bytearray()

                        # 计算需要下载的分段数量
                        chunks = (total_len + chunk_size - 1) // chunk_size  # 向上取整

                        logger.info(f"FileDownloader: 开始分段下载文件，总大小: {total_len} 字节，分 {chunks} 段下载")

                        # 分段下载
                        for i in range(chunks):
                            start_pos = i * chunk_size
                            # 最后一段可能不足 chunk_size
                            current_chunk_size = min(chunk_size, total_len - start_pos)

                            logger.info(f"FileDownloader: 下载第 {i+1}/{chunks} 段，起始位置: {start_pos}，大小: {current_chunk_size} 字节")

                            async with aiohttp.ClientSession() as session:
                                # 设置较长的超时时间
                                timeout = aiohttp.ClientTimeout(total=60)  # 1分钟

                                # 构造请求参数
                                json_param = {
                                    "AppID": app_id,
                                    "AttachId": attach_id,
                                    "DataLen": total_len,
                                    "Section": {
                                        "DataLen": current_chunk_size,
                                        "StartPos": start_pos
                                    },
                                    "UserName": "",  # 可选参数
                                    "Wxid": bot.wxid
                                }

                                logger.info(f"FileDownloader: 调用下载文件API: AttachId={attach_id}, 起始位置: {start_pos}, 大小: {current_chunk_size}")
                                response = await session.post(
                                    'http://127.0.0.1:9011/api/Tools/DownloadFile',
                                    json=json_param,
                                    timeout=timeout
                                )

                                # 处理响应
                                try:
                                    json_resp = await response.json()

                                    if json_resp.get("Success"):
                                        data = json_resp.get("Data")

                                        # 尝试从不同的响应格式中获取文件数据
                                        chunk_data = None
                                        if isinstance(data, dict):
                                            if "buffer" in data:
                                                chunk_data = base64.b64decode(data["buffer"])
                                            elif "data" in data and isinstance(data["data"], dict) and "buffer" in data["data"]:
                                                chunk_data = base64.b64decode(data["data"]["buffer"])
                                            else:
                                                try:
                                                    chunk_data = base64.b64decode(str(data))
                                                except:
                                                    logger.error(f"FileDownloader: 无法解析文件数据: {data}")
                                        elif isinstance(data, str):
                                            try:
                                                chunk_data = base64.b64decode(data)
                                            except:
                                                logger.error(f"FileDownloader: 无法解析文件数据字符串")

                                        if chunk_data:
                                            # 将分段数据添加到完整文件中
                                            file_data.extend(chunk_data)
                                            logger.info(f"FileDownloader: 第 {i+1}/{chunks} 段下载成功，大小: {len(chunk_data)} 字节")
                                        else:
                                            logger.warning(f"FileDownloader: 第 {i+1}/{chunks} 段数据为空")
                                    else:
                                        error_msg = json_resp.get("Message", "Unknown error")
                                        logger.error(f"FileDownloader: 第 {i+1}/{chunks} 段下载失败: {error_msg}")
                                except Exception as e:
                                    logger.error(f"FileDownloader: 解析第 {i+1}/{chunks} 段响应失败: {e}")

                        # 检查文件是否下载完整
                        if len(file_data) > 0:
                            logger.info(f"FileDownloader: 文件下载成功: AttachId={attach_id}, 实际大小: {len(file_data)} 字节")

                            # 保存文件
                            safe_title = self.get_safe_filename(title)
                            file_path = os.path.join(self.download_dir, f"{safe_title}.{file_extend}")
                            with open(file_path, "wb") as f:
                                f.write(file_data)

                            # 发送下载成功通知
                            await bot.send_text_message(
                                message["FromWxid"],
                                f"FileDownloader: 文件下载成功！\n文件名: {title}.{file_extend}\n保存路径: {file_path}\n文件大小: {len(file_data)} 字节"
                            )
                            logger.info(f"FileDownloader: 文件下载成功: {file_path}, 大小: {len(file_data)} 字节")
                        else:
                            logger.warning("FileDownloader: 文件数据为空")
                            await bot.send_text_message(
                                message["FromWxid"],
                                "FileDownloader: 文件下载失败，数据为空"
                            )
                    except Exception as e:
                        logger.error(f"FileDownloader: 下载文件时发生异常: {e}")
                        await bot.send_text_message(
                            message["FromWxid"],
                            f"FileDownloader: 下载文件时发生异常: {str(e)}"
                        )
                else:
                    # 如果不自动下载，只显示文件信息
                    await bot.send_text_message(
                        message["FromWxid"],
                        f"FileDownloader: 收到文件消息\n文件名: {title}.{file_extend}\n文件大小: {total_len} 字节\n\n发送「下载文件」命令可下载此文件"
                    )
        except Exception as e:
            logger.error(f"FileDownloader: 处理XML消息时发生错误: {str(e)}")

        return True  # 允许后续插件处理

    def get_safe_filename(self, filename: str) -> str:
        """生成安全的文件名，移除不允许的字符

        Args:
            filename: 原始文件名

        Returns:
            str: 安全的文件名
        """
        # 移除不允许的字符
        import re
        safe_name = re.sub(r'[\\/*?:"<>|]', '_', filename)
        # 限制文件名长度
        if len(safe_name) > 200:
            safe_name = safe_name[:200]
        return safe_name
