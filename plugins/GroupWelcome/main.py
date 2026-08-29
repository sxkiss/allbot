"""
@input: 系统进群 sysmsg（XML）、WechatAPIClient（send_app_message/get_chatroom_member_list/upload_file）
@output: 进群欢迎卡片消息（可选发送项目说明 PDF）
@position: 插件层进群欢迎逻辑，使用框架方法避免硬编码协议接口
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime
import os

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_system_message
from utils.plugin_base import PluginBase


class GroupWelcome(PluginBase):
    description = "进群欢迎"
    author = "allbot"
    version = "1.3.1"  # 改用框架方法，移除硬编码协议接口

    def __init__(self):
        super().__init__()

        with open("plugins/GroupWelcome/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        config = plugin_config.get("GroupWelcome", {})

        self.enable = bool(config.get("enable", True))
        self.welcome_message = config.get("welcome-message", "欢迎加入群聊！")
        self.url = config.get("url", "")
        # 是否发送PDF文件，默认为True
        self.send_file = config.get("send-file", False)

        # PDF文件路径
        self.pdf_path = os.path.join("plugins", "GroupWelcome", "temp", "allbot项目说明.pdf")
        # 只有在需要发送文件时才检查文件是否存在
        if self.send_file:
            if os.path.exists(self.pdf_path):
                logger.info(f"找到项目说明PDF文件: {self.pdf_path}")
            else:
                logger.warning(f"项目说明PDF文件不存在: {self.pdf_path}")
                
        # 协议差异由框架 client 统一封装，插件不读取协议版本也不直接调用底层协议 API

    @on_system_message
    async def group_welcome(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return

        if not message["IsGroup"]:
            return

        xml_content = str(message["Content"]).strip().replace("\n", "").replace("\t", "")
        root = ET.fromstring(xml_content)

        if root.tag != "sysmsg":
            return

        # 检查是否是进群消息
        if root.attrib.get("type") == "sysmsgtemplate":
            sys_msg_template = root.find("sysmsgtemplate")
            if sys_msg_template is None:
                return

            template = sys_msg_template.find("content_template")
            if template is None:
                return

            template_type = template.attrib.get("type")
            if template_type not in ["tmpl_type_profile", "tmpl_type_profilewithrevoke"]:
                return

            template_text = template.find("template").text

            if '"$names$"加入了群聊' in template_text:  # 直接加入群聊
                new_members = self._parse_member_info(root, "names")
            elif '"$username$"邀请"$names$"加入了群聊' in template_text:  # 通过邀请加入群聊
                new_members = self._parse_member_info(root, "names")
            elif '你邀请"$names$"加入了群聊' in template_text:  # 自己邀请成员加入群聊
                new_members = self._parse_member_info(root, "names")
            elif '"$adder$"通过扫描"$from$"分享的二维码加入群聊' in template_text:  # 通过二维码加入群聊
                new_members = self._parse_member_info(root, "adder")
            elif '"$adder$"通过"$from$"的邀请二维码加入群聊' in template_text:
                new_members = self._parse_member_info(root, "adder")
            else:
                logger.warning(f"未知的入群方式: {template_text}")
                return

            if not new_members:
                return

            for member in new_members:
                wxid = member["wxid"]
                nickname = member["nickname"]

                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                try:
                    # 获取用户头像
                    avatar_url = ""
                    try:
                        members = await bot.get_chatroom_member_list(message["FromWxid"])

                        def _extract_member_wxid(member_data: dict) -> str:
                            if not isinstance(member_data, dict):
                                return ""
                            for key in ("Wxid", "wxid", "UserName", "userName", "Username", "username", "user_name"):
                                value = member_data.get(key)
                                if isinstance(value, dict):
                                    value = value.get("string") or value.get("str") or value.get("value") or ""
                                if value:
                                    return str(value).strip()
                            return ""

                        def _extract_avatar_url(member_data: dict) -> str:
                            if not isinstance(member_data, dict):
                                return ""
                            for key in (
                                "BigHeadImgUrl",
                                "bigHeadImgUrl",
                                "SmallHeadImgUrl",
                                "smallHeadImgUrl",
                                "avatar",
                                "HeadImgUrl",
                                "headImgUrl",
                            ):
                                value = member_data.get(key)
                                if value:
                                    return str(value).strip()
                            return ""

                        for member_data in members or []:
                            if _extract_member_wxid(member_data) != wxid:
                                continue
                            avatar_url = _extract_avatar_url(member_data)
                            if avatar_url:
                                logger.info(f"成功获取到群成员 {nickname}({wxid}) 的头像地址")
                            break
                    except Exception as e:
                        logger.warning(f"获取用户头像失败: {e}")

                    # 准备发送欢迎消息
                    title = f"👏欢迎 {nickname} 加入群聊！🎉"
                    # 修改描述格式，将欢迎消息放在前面
                    description = f"{self.welcome_message}\n⌚时间：{now}"
                    
                    # 记录实际发送的内容
                    logger.info(f"欢迎消息内容: 标题=「{title}」 描述=「{description}」 链接=「{self.url}」")
                    
                    # 简化的XML结构
                    simple_xml = f"""<appmsg><title>{title}</title><des>{description}</des><type>5</type><url>{self.url}</url><thumburl>{avatar_url}</thumburl></appmsg>"""
                    
                    # 使用框架方法发送（避免硬编码协议接口）
                    await bot.send_app_message(message["FromWxid"], simple_xml, 5)
                    
                    # 根据配置决定是否发送项目说明PDF文件
                    if self.send_file:
                        await self.send_pdf_file(bot, message["FromWxid"])
                except Exception as e:
                    logger.error(f"发送欢迎消息失败: {e}")
                    # 如果获取失败，使用默认头像发送欢迎消息
                    title = f"👏欢迎 {nickname} 加入群聊！🎉"
                    # 修改描述格式，将欢迎消息放在前面
                    description = f"{self.welcome_message}\n⌚时间：{now}"
                    
                    # 记录实际发送的内容
                    logger.info(f"欢迎消息内容: 标题=「{title}」 描述=「{description}」 链接=「{self.url}」")
                    
                    # 简化的XML结构(无头像)
                    simple_xml = f"""<appmsg><title>{title}</title><des>{description}</des><type>5</type><url>{self.url}</url><thumburl></thumburl></appmsg>"""
                    
                    # 使用框架方法发送（避免硬编码协议接口）
                    await bot.send_app_message(message["FromWxid"], simple_xml, 5)
                    
                    # 根据配置决定是否发送项目说明PDF文件
                    if self.send_file:
                        await self.send_pdf_file(bot, message["FromWxid"])

    @staticmethod
    def _parse_member_info(root: ET.Element, link_name: str = "names") -> list[dict]:
        """解析新成员信息"""
        new_members = []
        try:
            # 查找指定链接中的成员列表
            names_link = root.find(f".//link[@name='{link_name}']")
            if names_link is None:
                return new_members

            memberlist = names_link.find("memberlist")

            if memberlist is None:
                return new_members

            for member in memberlist.findall("member"):
                username = member.find("username").text
                nickname = member.find("nickname").text
                new_members.append({
                    "wxid": username,
                    "nickname": nickname
                })

        except Exception as e:
            logger.warning(f"解析新成员信息失败: {e}")

        return new_members

    async def send_pdf_file(self, bot: WechatAPIClient, to_wxid: str):
        """发送项目说明PDF文件"""
        try:
            # 检查文件是否存在
            if not os.path.exists(self.pdf_path):
                logger.error(f"项目说明PDF文件不存在: {self.pdf_path}")
                return

            # 读取文件内容
            with open(self.pdf_path, "rb") as f:
                file_data = f.read()

            # 获取文件名和扩展名
            file_name = os.path.basename(self.pdf_path)
            file_extension = os.path.splitext(file_name)[1][1:]  # 去掉点号

            # 上传文件
            logger.info(f"开始上传项目说明PDF文件: {file_name}")
            file_info = await bot.upload_file(file_data)
            logger.info(f"项目说明PDF文件上传成功: {file_info}")

            # 从文件信息中提取必要的字段
            media_id = file_info.get('mediaId')
            total_len = file_info.get('totalLen', len(file_data))

            logger.info(f"文件信息: mediaId={media_id}, totalLen={total_len}")

            # 构造XML消息
            xml = f"""<appmsg>
    <title>{file_name}</title>
    <type>6</type>
    <appattach>
        <totallen>{total_len}</totallen>
        <attachid>{media_id}</attachid>
        <fileext>{file_extension}</fileext>
    </appattach>
</appmsg>"""

            # 发送文件消息
            logger.info(f"开始发送项目说明PDF文件: {file_name}")
            await bot.send_app_message(to_wxid, xml, 6)
            logger.info("项目说明PDF文件发送完成")

        except Exception as e:
            logger.error(f"发送项目说明PDF文件失败: {e}")
