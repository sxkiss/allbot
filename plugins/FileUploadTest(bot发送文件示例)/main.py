import os
from loguru import logger
from typing import Dict, Any

from WechatAPI import WechatAPIClient
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message

class FileUploadTest(PluginBase):
    """文件上传测试插件"""

    def __init__(self):
        super().__init__()
        self.name = "FileUploadTest"
        self.description = "测试文件上传和发送功能"
        self.version = "0.1.0"
        self.author = "Augment Agent"
        self.enable = True

        # 创建files目录用于存放测试文件
        self.files_dir = os.path.join(os.path.dirname(__file__), "files")
        os.makedirs(self.files_dir, exist_ok=True)
        logger.info(f"文件测试目录创建成功: {self.files_dir}")

    @on_text_message
    async def send_file_test(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        """测试发送文件功能"""
        if not self.enable:
            return True

        content = str(message.get("Content", "")).strip()
        # 检查是否是发送文件命令
        if content.startswith("发送文件") or content.startswith("发送测试文件"):
            # 如果是简单的发送文件命令，则使用默认文件
            pass
        else:
            return True

        # 获取发送者wxid
        from_wxid = message.get("FromWxid")

        # 解析命令，检查是否指定了文件名
        parts = content.split(" ", 1)
        if len(parts) > 1 and parts[1].strip():
            # 指定了文件名
            specified_file = parts[1].strip()
            file_path = os.path.join(self.files_dir, specified_file)
            if not os.path.exists(file_path):
                await bot.send_text_message(from_wxid, f"指定的文件 '{specified_file}' 不存在。\n请使用命令：文件列表 查看可用文件。")
                return False
            logger.info(f"使用指定文件: {file_path}")
        else:
            # 没有指定文件名，尝试使用test.txt
            test_file_path = os.path.join(self.files_dir, "test.txt")
            if os.path.exists(test_file_path):
                file_path = test_file_path
                logger.info(f"使用files目录中的测试文件: {file_path}")
            else:
                # 如果没有test.txt，检查目录中是否有其他文件
                files = os.listdir(self.files_dir)
                if files:
                    file_path = os.path.join(self.files_dir, files[0])
                    logger.info(f"使用files目录中的第一个文件: {file_path}")
                else:
                    # 如果目录中没有文件，则使用默认的test.txt
                    file_path = os.path.join(os.path.dirname(__file__), "test.txt")
                    logger.info(f"使用默认测试文件: {file_path}")

        try:
            # 发送提示消息
            await bot.send_text_message(from_wxid, "开始测试文件上传和发送功能...")

            # 读取文件内容
            with open(file_path, "rb") as f:
                file_data = f.read()

            # 获取文件名和扩展名
            file_name = os.path.basename(file_path)
            file_extension = os.path.splitext(file_name)[1][1:]  # 去掉点号

            # 上传文件
            logger.info(f"开始上传文件: {file_name}")
            file_info = await bot.upload_file(file_data)
            logger.info(f"文件上传成功: {file_info}")

            # 构造XML消息
            # 从文件信息中提取必要的字段
            media_id = file_info.get('mediaId')
            total_len = file_info.get('totalLen', len(file_data))

            logger.info(f"文件信息: mediaId={media_id}, totalLen={total_len}")

            xml = f"""<appmsg appid="" sdkver="0">
    <title>{file_name}</title>
    <des></des>
    <action></action>
    <type>6</type>
    <showtype>0</showtype>
    <content></content>
    <url></url>
    <appattach>
        <totallen>{total_len}</totallen>
        <attachid>{media_id}</attachid>
        <fileext>{file_extension}</fileext>
    </appattach>
    <md5></md5>
</appmsg>"""

            # 发送文件消息
            logger.info(f"开始发送文件消息: {file_name}")
            result = await bot._send_cdn_file_msg(from_wxid, xml)
            logger.info(f"文件消息发送结果: {result}")

            # 发送成功提示
            await bot.send_text_message(from_wxid, f"文件 {file_name} 发送成功！")

            # 返回False阻止消息继续传递
            return False
        except Exception as e:
            logger.error(f"发送文件测试失败: {e}")
            await bot.send_text_message(from_wxid, f"发送文件测试失败: {e}")
            # 返回False阻止消息继续传递
            return False

    @on_text_message
    async def list_files(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        """列出文件目录中的文件"""
        if not self.enable:
            return True

        content = str(message.get("Content", "")).strip()
        if content != "文件列表":
            return True

        # 获取发送者wxid
        from_wxid = message.get("FromWxid")

        try:
            # 获取files目录中的所有文件
            files = os.listdir(self.files_dir)

            if not files:
                await bot.send_text_message(from_wxid, "文件目录中没有文件。\n请将文件放入以下目录：\n" + self.files_dir)
                return False

            # 构造文件列表消息
            file_list = "文件目录中的文件列表：\n"
            for i, file in enumerate(files, 1):
                file_path = os.path.join(self.files_dir, file)
                file_size = os.path.getsize(file_path)
                file_list += f"{i}. {file} ({self._format_size(file_size)})\n"

            file_list += "\n要发送文件，请使用命令：发送文件测试"

            await bot.send_text_message(from_wxid, file_list)
            return False
        except Exception as e:
            logger.error(f"获取文件列表失败: {e}")
            await bot.send_text_message(from_wxid, f"获取文件列表失败: {e}")
            return False

    def _format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
