"""
@input: WechatAPIClient/Client869 实例、原始 AddMsg 消息字典与本地 files 缓存目录
@output: 旧版 XYBot 消息处理实现（图片/语音/XML/文件等），供 utils.xybot.core 兼容委托；文件消息解析对缺失 `fileext` 的 XML 也能继续提取 `title/attachid` 并触发后续下载
@position: 历史兼容层：承载未拆分的消息处理逻辑与媒体落盘（files/）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import base64
import hashlib
import html
import io
import os
import re
import shutil
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from database.contacts_db import get_contact_from_db, update_contact_in_db
from database.messsagDB import MessageDB
from utils.event_manager import EventManager
from WechatAPI import WechatAPIClient
from WechatAPI.Client.protect import protector
from database.message_counter import get_instance as get_message_counter


class XYBot:
    def __init__(self, bot_client: WechatAPIClient):
        self.bot = bot_client
        self.wxid = None
        self.nickname = None
        self.alias = None
        self.phone = None

        # 打印当前工作目录，便于调试
        import os

        logger.debug(f"当前工作目录: {os.getcwd()}")

        # 检查配置文件是否存在
        config_path = "main_config.toml"
        if not os.path.exists(config_path):
            logger.error(f"配置文件 {config_path} 不存在")
            main_config = {}
        else:
            logger.debug(
                f"配置文件 {config_path} 存在，大小: {os.path.getsize(config_path)} 字节"
            )
            try:
                with open(config_path, "rb") as f:
                    main_config = tomllib.load(f)
                    # 打印配置文件的所有键
                    logger.debug(f"配置文件的所有键: {list(main_config.keys())}")
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
                main_config = {}

        # 读取 XYBot 配置段
        xybot_config = main_config.get("XYBot", {})
        if not isinstance(xybot_config, dict):
            xybot_config = {}

        # 管理员列表：优先读取 [XYBot].admins，其次读取顶层 admins（兼容不同写法）
        admins = xybot_config.get("admins")
        if admins is None:
            admins = main_config.get("admins", [])
        self.admins = admins if isinstance(admins, list) else []

        self.ignore_protection = xybot_config.get(
            "ignore-protection", False
        )

        # 读取群聊唤醒词配置
        self.group_wakeup_words = xybot_config.get("group-wakeup-words", ["bot"])
        self.enable_group_wakeup = xybot_config.get("enable-group-wakeup", True)
        logger.info(
            f"群聊唤醒词: {self.group_wakeup_words}, 启用状态: {self.enable_group_wakeup}"
        )

        # 从配置文件中读取消息过滤设置
        try:
            # 尝试从顶层读取设置
            if "ignore-mode" in main_config:
                self.ignore_mode = main_config["ignore-mode"]
                self.whitelist = main_config["whitelist"]
                self.blacklist = main_config["blacklist"]
            # 如果顶层没有，尝试从AutoRestart部分读取
            elif (
                "AutoRestart" in main_config
                and "ignore-mode" in main_config["AutoRestart"]
            ):
                self.ignore_mode = main_config["AutoRestart"]["ignore-mode"]
                self.whitelist = main_config["AutoRestart"]["whitelist"]
                self.blacklist = main_config["AutoRestart"]["blacklist"]
            else:
                # 如果都没有，使用默认值
                raise KeyError("ignore-mode not found in config")
        except KeyError as e:
            # 如果读取失败，使用默认值
            self.ignore_mode = "None"
            self.whitelist = []
            self.blacklist = []

        # 记录配置信息
        logger.info(f"消息过滤模式: {self.ignore_mode}")
        logger.info(f"白名单: {self.whitelist}")
        logger.info(f"黑名单: {self.blacklist}")

        self.msg_db = MessageDB()

    def update_profile(self, wxid: str, nickname: str, alias: str, phone: str):
        """更新机器人信息"""
        self.wxid = wxid
        self.nickname = nickname
        self.alias = alias
        self.phone = phone

    def is_logged_in(self):
        """检查机器人是否已登录

        Returns:
            bool: 如果已登录返回true，否则返回false
        """
        return self.wxid is not None

    async def get_chatroom_member_list(self, group_wxid: str):
        """获取群成员列表

        Args:
            group_wxid: 群聊的wxid

        Returns:
            list: 群成员列表
        """
        if not group_wxid.endswith("@chatroom"):
            logger.error(f"无效的群ID: {group_wxid}，只有群聊才能获取成员列表")
            return []

        try:
            logger.info(f"开始获取群 {group_wxid} 的成员列表")

            # 直接调用微信API获取群成员列表
            try:
                import json

                import aiohttp

                # 获取微信API的基本配置
                api_base = "http://127.0.0.1:9011"
                if hasattr(self.bot, "ip") and hasattr(self.bot, "port"):
                    api_base = f"http://{self.bot.ip}:{self.bot.port}"

                # 确定API路径前缀
                api_prefix = ""

                # 先检查是否有显式设置的前缀
                if hasattr(self.bot, "api_prefix"):
                    api_prefix = self.bot.api_prefix
                elif hasattr(self.bot, "_api_prefix"):
                    api_prefix = self.bot._api_prefix

                # 如果没有显式设置，则根据协议版本确定
                if api_prefix == "":
                    # 使用协议配置管理器获取 API 前缀
                    try:
                        import tomllib
                        from utils.protocol_config import ProtocolConfig

                        with open("main_config.toml", "rb") as f:
                            config = tomllib.load(f)
                            protocol_version = config.get("Protocol", {}).get(
                                "version", "pad"
                            )

                            # 使用协议配置管理器获取前缀（避免硬编码）
                            api_prefix = ProtocolConfig.get_api_prefix(protocol_version)
                            logger.info(f"使用 {protocol_version} 协议前缀: {api_prefix}")
                    except Exception as e:
                        logger.warning(f"读取协议版本失败，使用默认前缀: {e}")
                        # 使用默认前缀（如果 ProtocolConfig 导入失败，使用硬编码默认值）
                        api_prefix = "/api"
                        logger.info(f"使用默认协议前缀: {api_prefix}")

                # 获取当前登录的wxid
                wxid = ""
                if hasattr(self.bot, "wxid"):
                    wxid = self.bot.wxid

                logger.info(
                    f"使用API路径: {api_base}{api_prefix}/Group/GetChatRoomMemberDetail"
                )

                # 直接调用API获取群成员
                async with aiohttp.ClientSession() as session:
                    json_param = {"QID": group_wxid, "Wxid": wxid}
                    logger.info(f"发送请求参数: {json.dumps(json_param)}")

                    response = await session.post(
                        f"{api_base}{api_prefix}/Group/GetChatRoomMemberDetail",
                        json=json_param,
                        headers={"Content-Type": "application/json"},
                    )

                    # 检查响应状态
                    if response.status != 200:
                        logger.error(
                            f"获取群成员列表失败: HTTP状态码 {response.status}"
                        )
                        return []

                    # 解析响应数据
                    try:
                        json_resp = await response.json()
                        logger.info(f"收到API响应: {json.dumps(json_resp)[:200]}...")

                        if json_resp.get("Success"):
                            # 处理成功响应
                            members_data = []

                            # 根据实际响应结构提取成员列表
                            if (
                                json_resp.get("Data")
                                and json_resp["Data"].get("NewChatroomData")
                                and json_resp["Data"]["NewChatroomData"].get(
                                    "ChatRoomMember"
                                )
                            ):
                                members_data = json_resp["Data"]["NewChatroomData"][
                                    "ChatRoomMember"
                                ]
                                logger.info(
                                    f"从 NewChatroomData.ChatRoomMember 获取到 {len(members_data)} 个成员"
                                )
                            elif json_resp.get("Data") and json_resp["Data"].get(
                                "ChatRoomMember"
                            ):
                                members_data = json_resp["Data"]["ChatRoomMember"]
                                logger.info(
                                    f"从 Data.ChatRoomMember 获取到 {len(members_data)} 个成员"
                                )
                            elif json_resp.get("Data") and isinstance(
                                json_resp["Data"], list
                            ):
                                members_data = json_resp["Data"]
                                logger.info(
                                    f"从 Data 数组获取到 {len(members_data)} 个成员"
                                )

                            logger.info(
                                f"成功获取群 {group_wxid} 的成员列表，共 {len(members_data)} 个成员"
                            )

                            # 处理成员信息，确保每个成员都有基本字段
                            members = []
                            for member in members_data:
                                # 确保每个成员都有wxid字段
                                if not member.get("wxid") and member.get("Wxid"):
                                    member["wxid"] = member["Wxid"]

                                # 确保每个成员都有nickname字段
                                if not member.get("nickname"):
                                    if member.get("NickName"):
                                        member["nickname"] = member["NickName"]
                                    else:
                                        member["nickname"] = member.get(
                                            "wxid", "Unknown"
                                        )

                                # 处理头像字段
                                if not member.get("avatar"):
                                    if member.get("BigHeadImgUrl"):
                                        member["avatar"] = member["BigHeadImgUrl"]
                                    elif member.get("SmallHeadImgUrl"):
                                        member["avatar"] = member["SmallHeadImgUrl"]

                                members.append(member)

                            return members
                        else:
                            error_msg = (
                                json_resp.get("Message")
                                or json_resp.get("message")
                                or "未知错误"
                            )
                            logger.warning(
                                f"获取群 {group_wxid} 成员列表失败: {error_msg}"
                            )
                            return []
                    except Exception as e:
                        logger.error(f"解析群成员响应数据失败: {str(e)}")
                        return []
            except Exception as e:
                logger.error(f"调用API获取群 {group_wxid} 成员列表失败: {str(e)}")
                return []
        except Exception as e:
            logger.error(f"获取群成员列表时发生异常: {str(e)}")
            return []

    async def update_contact_info(self, wxid: str):
        """更新联系人信息

        Args:
            wxid: 联系人的wxid
        """
        try:
            # 先检查数据库中是否已有该联系人的信息
            existing_contact = get_contact_from_db(wxid)

            # 如果数据库中没有该联系人的信息，或者信息不完整，则从 API 获取
            if not existing_contact or not existing_contact.get("nickname"):
                # 从 API 获取联系人信息
                try:
                    # 如果是群聊，不获取详细信息
                    if wxid.endswith("@chatroom"):
                        contact_info = {"wxid": wxid, "nickname": wxid, "type": "group"}
                        # 更新到数据库
                        update_contact_in_db(contact_info)
                        logger.debug(f"已在消息处理中更新群聊 {wxid} 的基本信息")
                    else:
                        # 获取联系人详细信息
                        logger.debug(f"开始获取联系人 {wxid} 的详细信息")
                        try:
                            detail = await self.bot.get_contract_detail(wxid)
                            logger.debug(f"获取到联系人 {wxid} 的详细信息: {detail}")

                            if detail:
                                # 处理返回结果
                                if isinstance(detail, list) and len(detail) > 0:
                                    detail_item = detail[0]
                                    logger.debug(
                                        f"联系人 {wxid} 详情项类型: {type(detail_item)}"
                                    )

                                    if isinstance(detail_item, dict):
                                        # 记录详细的字段信息，帮助调试
                                        logger.debug(
                                            f"联系人 {wxid} 详情字段: {list(detail_item.keys())}"
                                        )

                                        # 检查nickname字段的类型和值
                                        nickname_value = detail_item.get("nickname")
                                        if nickname_value is None:
                                            # 尝试其他可能的字段名
                                            nickname_value = detail_item.get("NickName")
                                            if nickname_value is None:
                                                logger.warning(
                                                    f"联系人 {wxid} 没有找到nickname或NickName字段"
                                                )
                                            else:
                                                logger.debug(
                                                    f"联系人 {wxid} 使用NickName字段: {nickname_value}"
                                                )
                                        else:
                                            logger.debug(
                                                f"联系人 {wxid} 使用nickname字段: {nickname_value}"
                                            )

                                        # 如果nickname是字典类型，尝试获取其中的string字段
                                        if isinstance(nickname_value, dict):
                                            logger.debug(
                                                f"联系人 {wxid} 的nickname是字典类型: {nickname_value}"
                                            )
                                            nickname_string = nickname_value.get(
                                                "string"
                                            )
                                            if nickname_string:
                                                nickname_value = nickname_string
                                                logger.debug(
                                                    f"从字典中提取nickname.string: {nickname_string}"
                                                )

                                        # 处理头像字段 - 优先使用BigHeadImgUrl或SmallHeadImgUrl
                                        avatar_value = detail_item.get(
                                            "BigHeadImgUrl", ""
                                        )
                                        if not avatar_value:
                                            avatar_value = detail_item.get(
                                                "SmallHeadImgUrl", ""
                                            )
                                        if not avatar_value:
                                            # 如果没有直接的URL，尝试使用avatar字段
                                            avatar_value = detail_item.get("avatar", "")
                                            if isinstance(avatar_value, dict):
                                                avatar_value = avatar_value.get(
                                                    "string", ""
                                                )

                                        logger.debug(
                                            f"联系人 {wxid} 的头像地址: {avatar_value}"
                                        )

                                        # 处理备注字段
                                        remark_value = detail_item.get("remark", "")
                                        if remark_value is None or not remark_value:
                                            remark_value = detail_item.get("Remark", "")
                                        if isinstance(remark_value, dict):
                                            remark_value = remark_value.get(
                                                "string", ""
                                            )

                                        # 处理微信号字段
                                        alias_value = detail_item.get("alias", "")
                                        if alias_value is None or not alias_value:
                                            alias_value = detail_item.get("Alias", "")
                                        if isinstance(alias_value, dict):
                                            alias_value = alias_value.get("string", "")

                                        # 构建联系人信息
                                        contact_info = {
                                            "wxid": wxid,
                                            "nickname": (
                                                nickname_value
                                                if nickname_value
                                                else wxid
                                            ),
                                            "avatar": avatar_value,
                                            "remark": remark_value,
                                            "alias": alias_value,
                                        }
                                        logger.debug(
                                            f"解析联系人 {wxid} 详情成功: {contact_info}"
                                        )
                                    else:
                                        logger.warning(
                                            f"联系人 {wxid} 详情格式不是字典: {detail_item}"
                                        )
                                        # 创建基本联系人信息
                                        contact_info = {
                                            "wxid": wxid,
                                            "nickname": wxid,
                                            "type": "friend",
                                        }
                                elif isinstance(detail, dict):
                                    # 记录详细的字段信息，帮助调试
                                    logger.debug(
                                        f"联系人 {wxid} 详情字段(字典格式): {list(detail.keys())}"
                                    )

                                    # 检查nickname字段的类型和值
                                    nickname_value = detail.get("nickname")
                                    if nickname_value is None:
                                        # 尝试其他可能的字段名
                                        nickname_value = detail.get("NickName")
                                        if nickname_value is None:
                                            logger.warning(
                                                f"联系人 {wxid} 没有找到nickname或NickName字段"
                                            )
                                        else:
                                            logger.debug(
                                                f"联系人 {wxid} 使用NickName字段: {nickname_value}"
                                            )
                                    else:
                                        logger.debug(
                                            f"联系人 {wxid} 使用nickname字段: {nickname_value}"
                                        )

                                    # 如果nickname是字典类型，尝试获取其中的string字段
                                    if isinstance(nickname_value, dict):
                                        logger.debug(
                                            f"联系人 {wxid} 的nickname是字典类型: {nickname_value}"
                                        )
                                        nickname_string = nickname_value.get("string")
                                        if nickname_string:
                                            nickname_value = nickname_string
                                            logger.debug(
                                                f"从字典中提取nickname.string: {nickname_string}"
                                            )

                                    # 处理头像字段 - 优先使用BigHeadImgUrl或SmallHeadImgUrl
                                    avatar_value = detail.get("BigHeadImgUrl", "")
                                    if not avatar_value:
                                        avatar_value = detail.get("SmallHeadImgUrl", "")
                                    if not avatar_value:
                                        # 如果没有直接的URL，尝试使用avatar字段
                                        avatar_value = detail.get("avatar", "")
                                        if isinstance(avatar_value, dict):
                                            avatar_value = avatar_value.get(
                                                "string", ""
                                            )

                                    logger.debug(
                                        f"联系人 {wxid} 的头像地址(字典格式): {avatar_value}"
                                    )

                                    # 处理备注字段
                                    remark_value = detail.get("remark", "")
                                    if remark_value is None or not remark_value:
                                        remark_value = detail.get("Remark", "")
                                    if isinstance(remark_value, dict):
                                        remark_value = remark_value.get("string", "")

                                    # 处理微信号字段
                                    alias_value = detail.get("alias", "")
                                    if alias_value is None or not alias_value:
                                        alias_value = detail.get("Alias", "")
                                    if isinstance(alias_value, dict):
                                        alias_value = alias_value.get("string", "")

                                    # 构建联系人信息
                                    contact_info = {
                                        "wxid": wxid,
                                        "nickname": (
                                            nickname_value if nickname_value else wxid
                                        ),
                                        "avatar": avatar_value,
                                        "remark": remark_value,
                                        "alias": alias_value,
                                    }
                                    logger.debug(
                                        f"解析联系人 {wxid} 详情成功(字典格式): {contact_info}"
                                    )
                                else:
                                    logger.warning(
                                        f"联系人 {wxid} 详情格式不支持: {type(detail)}"
                                    )
                                    # 创建基本联系人信息
                                    contact_info = {
                                        "wxid": wxid,
                                        "nickname": wxid,
                                        "type": "friend",
                                    }
                            else:
                                logger.warning(
                                    f"无法获取联系人 {wxid} 的详细信息，API返回空数据"
                                )
                                # 创建基本联系人信息
                                contact_info = {
                                    "wxid": wxid,
                                    "nickname": wxid,
                                    "type": "friend",
                                }

                            # 更新到数据库
                            update_contact_in_db(contact_info)
                            logger.debug(f"已在消息处理中更新联系人 {wxid} 的信息")
                        except Exception as e:
                            logger.error(f"调用API获取联系人 {wxid} 详情失败: {str(e)}")
                            # 创建基本联系人信息
                            contact_info = {
                                "wxid": wxid,
                                "nickname": wxid,
                                "type": "friend",
                            }
                            # 仍然更新到数据库，确保至少有基本信息
                            update_contact_in_db(contact_info)
                            logger.debug(f"已在消息处理中更新联系人 {wxid} 的基本信息")
                except Exception as e:
                    logger.error(f"在消息处理中获取联系人 {wxid} 信息失败: {str(e)}")
                    # 创建基本联系人信息并保存
                    contact_info = {
                        "wxid": wxid,
                        "nickname": wxid,
                        "type": "friend" if not wxid.endswith("@chatroom") else "group",
                    }
                    update_contact_in_db(contact_info)
                    logger.debug(
                        f"已在消息处理中更新联系人 {wxid} 的基本信息(异常处理)"
                    )
        except Exception as e:
            logger.error(f"更新联系人信息时发生异常: {str(e)}")

    async def process_message(self, message: Dict[str, Any]):
        """处理接收到的消息"""

        # 统计所有消息
        message_counter = get_message_counter()
        message_counter.increment()

        # 简单WebSocket消息转换 - 最小化修改
        # 只做必要的字段映射，保持原有处理逻辑不变
        if "msgId" in message and "MsgId" not in message:
            message["MsgId"] = message.get("msgId")

        if "category" in message and "MsgType" not in message:
            message["MsgType"] = message.get("category")

        if "content" in message and not message.get("Content"):
            message["Content"] = {"string": message.get("content", "")}

        if "sender" in message and isinstance(message["sender"], dict):
            if "id" in message["sender"]:
                message["FromWxid"] = message["sender"]["id"]
                message["FromUserName"] = {"string": message["sender"]["id"]}

        if "ToWxid" not in message:
            message["ToWxid"] = ""

        # 如果缺少MsgSource，提供一个简单的空结构避免错误
        if "MsgSource" not in message:
            message["MsgSource"] = "<msgsource></msgsource>"

        # 保留原有的消息处理逻辑
        msg_type = message.get("MsgType")

        # 预处理消息
        # 确保 FromWxid 始终是字符串，默认为空字符串
        from_user = message.get("FromUserName", {})
        if isinstance(from_user, dict):
            message["FromWxid"] = from_user.get("string", "")
        else:
            message["FromWxid"] = str(from_user) if from_user else ""
        message.pop("FromUserName", None)

        # 确保 ToWxid 始终是字符串，默认为空字符串
        to_wxid = message.get("ToWxid", {})
        if isinstance(to_wxid, dict):
            message["ToWxid"] = to_wxid.get("string", "")
        else:
            message["ToWxid"] = str(to_wxid) if to_wxid else ""

        # 处理一下自己发的消息
        to_wxid = message.get("ToWxid", "")
        if (
            message.get("FromWxid") == self.wxid
            and isinstance(to_wxid, str)
            and to_wxid.endswith("@chatroom")
        ):
            message["FromWxid"], message["ToWxid"] = (
                message["ToWxid"],
                message["FromWxid"],
            )

        # 异步更新发送者联系人信息
        from_wxid = message.get("FromWxid", "")
        if from_wxid and from_wxid != self.wxid:
            # 如果是群聊，只更新群聊本身信息
            if from_wxid.endswith("@chatroom"):
                logger.info(f"开始异步更新群聊信息: {from_wxid}")
                update_task = asyncio.create_task(self.update_contact_info(from_wxid))
                # 添加回调以记录完成状态
                update_task.add_done_callback(
                    lambda t: logger.info(
                        f"完成群聊信息更新: {from_wxid}, 状态: {'success' if not t.exception() else f'error: {t.exception()}'}"
                    )
                )
            # 如果是私聊，更新发送者信息
            elif not from_wxid.endswith("@chatroom"):
                logger.info(f"开始异步更新发送者联系人信息: {from_wxid}")
                update_task = asyncio.create_task(self.update_contact_info(from_wxid))
                # 添加回调以记录完成状态
                update_task.add_done_callback(
                    lambda t: logger.info(
                        f"完成发送者联系人信息更新: {from_wxid}, 状态: {'success' if not t.exception() else f'error: {t.exception()}'}"
                    )
                )

        # 根据消息类型触发不同的事件
        if msg_type == 1:  # 文本消息
            await self.process_text_message(message)
        elif msg_type == 3:  # 图片消息
            await self.process_image_message(message)
        elif msg_type == 34:  # 语音消息
            await self.process_voice_message(message)
        elif msg_type == 43:  # 视频消息
            await self.process_video_message(message)
        elif msg_type == 47:  # 表情消息
            await self.process_emoji_message(message)
        elif msg_type == 49:  # xml消息
            await self.process_xml_message(message)
        elif msg_type == 10002:  # 系统消息
            await self.process_system_message(message)
        elif msg_type == 37:  # 好友请求
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("friend_request", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")
        elif msg_type == 51:
            pass
        else:
            logger.info("未知的消息类型: {}", message)

    async def process_text_message(self, message: Dict[str, Any]):
        """处理文本消息"""
        message["Content"] = message.get("Content", {}).get("string", "")

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":\n", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

            # Web 对话：固定为最高权限发送人（仅影响权限判断，不影响回复路由）
            if message.get("platform") == "web" and self.admins:
                admin_wxid = self.admins[0]
                if admin_wxid and isinstance(admin_wxid, str):
                    message["SenderWxid"] = admin_wxid

        try:
            root = ET.fromstring(message.get("MsgSource", ""))
            ats = (
                root.find("atuserlist").text
                if root.find("atuserlist") is not None
                else ""
            )
        except Exception as e:
            if not message.get("MsgSource"):  # 空字符串常见于普通文本消息
                logger.debug("MsgSource 为空，跳过 atuserlist 解析，无需报错。")
            else:
                logger.warning(f"MsgSource 解析失败，内容非有效XML: {message.get('MsgSource')}")
                logger.debug(f"解析文本消息失败: {e}")
            ats = ""

        if ats:
            ats = ats.strip(",").split(",")
        else:
            ats = []
        message["Ats"] = ats if ats and ats[0] != "" else []

        bot_wxid = (getattr(self.bot, "wxid", None) or self.wxid or "").strip()

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        if bot_wxid and bot_wxid in message.get("Ats", []):
            logger.info(
                "收到被@消息: 消息ID:{} 来自:{} 发送人:{} @:{} 内容:{}",
                message.get("MsgId", ""),
                message["FromWxid"],
                message["SenderWxid"],
                message["Ats"],
                message["Content"],
            )
            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    # 先检查消息是否包含唤醒词
                    wakeup_handled = await self.check_wakeup_words(message)

                    # 如果没有唤醒词或唤醒词处理返回继续，则使用默认的at_message事件
                    if not wakeup_handled:
                        logger.debug("未检测到唤醒词，使用默认的at_message事件处理")
                        await EventManager.emit("at_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")
            return

        logger.info(
            "收到文本消息: 消息ID:{} 来自:{} 发送人:{} @:{} 内容:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Ats"],
            message["Content"],
        )

        # 检查是否需要处理该消息（群聊唤醒词检查）
        should_process = await self.check_group_wakeup_word(message)

        # 群聊消息和私聊消息都处理
        # 无论是否@机器人，都处理消息
        if should_process and self.ignore_check(
            message["FromWxid"], message["SenderWxid"]
        ):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("text_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_image_message(self, message: Dict[str, Any]):
        """处理图片消息"""
        image_base64 = message.get("ImageBase64")
        message["Content"] = (
            (image_base64 if isinstance(image_base64, str) else message.get("Content", {}).get("string", ""))
            .replace("\n", "")
            .replace("\t", "")
        )

        if message["FromWxid"].endswith("@chatroom"):
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info(
            "收到图片消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Content"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message.get("MsgSource", ""),
            is_group=message["IsGroup"],
        )

        aeskey, cdnmidimgurl, length, md5 = None, None, None, None
        try:
            root = ET.fromstring(message["Content"])
            img_element = root.find("img")
            if img_element is not None:
                aeskey = img_element.get("aeskey")
                cdnmidimgurl = img_element.get("cdnbigimgurl") or img_element.get("cdnmidimgurl")
                length = img_element.get("length")
                md5 = img_element.get("md5")
                logger.debug(
                    f"解析图片XML成功: aeskey={aeskey}, length={length}, md5={md5}"
                )

                # 保存MD5信息到消息中，方便后续使用
                message["ImageMD5"] = md5
        except Exception as e:
            logger.error("解析图片消息失败: {}, 内容: {}", e, message["Content"])
            aeskey = cdnmidimgurl = None
            length = md5 = None

        # 如果适配器提供了本地缓存路径，优先建立 files 映射，方便后续引用
        self._sync_resource_media(message)
        self._ensure_image_xml(message)
        self._embed_base64_from_resource(message)

        # 直接使用 get_msg_image 下载高清图片（新版接口，返回二进制数据）
        try:
            if aeskey and cdnmidimgurl:
                logger.debug("直接使用get_msg_image下载图片")
                image_data = await self.bot.get_msg_image(aeskey, cdnmidimgurl)
                if image_data:
                    import base64

                    message["Content"] = base64.b64encode(image_data).decode("utf-8")
                else:
                    logger.error("get_msg_image 返回空数据")
        except Exception as e:
            logger.error(f"下载图片失败: {e}")

        # 如果成功获取图片数据且有MD5值，保存到files目录
        image_data = None
        if (
            message["Content"]
            and isinstance(message["Content"], str)
            and message["ImageMD5"]
        ):
            try:
                # 解码base64获取图片数据
                import base64

                image_data = base64.b64decode(message["Content"])

                # 确保files目录存在
                files_dir = os.path.join(os.getcwd(), "files")
                os.makedirs(files_dir, exist_ok=True)

                # 根据MD5值生成文件名
                file_extension = self._get_image_extension(image_data)
                file_name = f"{message['ImageMD5']}.{file_extension}"
                file_path = os.path.join(files_dir, file_name)

                # 保存图片文件
                with open(file_path, "wb") as f:
                    f.write(image_data)
                logger.info(f"图片已保存到: {file_path}")

                # 将文件路径添加到消息中，方便后续使用
                message["ImagePath"] = file_path
            except Exception as save_error:
                logger.error(f"保存图片文件失败: {save_error}")

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("image_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    def _get_image_extension(self, image_data):
        """根据图片数据判断文件扩展名"""
        try:
            import io

            from PIL import Image

            # 尝试打开图片并获取格式
            img = Image.open(io.BytesIO(image_data))
            fmt = img.format.lower() if img.format else "jpg"
            return fmt
        except Exception as e:
            logger.error(f"获取图片格式失败: {e}")
            return "jpg"  # 默认返回jpg

    def _sync_resource_media(self, message: Dict[str, Any]) -> None:
        path = message.get("ResourcePath")
        if not path:
            return
        resolved = Path(path)
        if not resolved.exists():
            logger.debug(f"ResourcePath 不存在: {resolved}")
            return
        md5_value = message.get("ImageMD5")
        if not md5_value:
            md5_value = self._calculate_file_md5(resolved)
            if not md5_value:
                return
            message["ImageMD5"] = md5_value
        try:
            files_dir = Path(os.getcwd()) / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            suffix = resolved.suffix or ".bin"
            dest = files_dir / f"{md5_value}{suffix}"
            if not dest.exists():
                shutil.copy2(resolved, dest)
                logger.info(f"同步本地媒体到 {dest}")
            message["ImagePath"] = str(resolved)
        except Exception as exc:
            logger.warning(f"同步 ResourcePath 媒体失败: {exc}")

    @staticmethod
    def _calculate_file_md5(path: Path) -> str:
        try:
            hasher = hashlib.md5()
            with open(path, "rb") as source:
                for chunk in iter(lambda: source.read(8192), b""):
                    if chunk:
                        hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    def _ensure_image_xml(self, message: Dict[str, Any]) -> None:
        content = (message.get("Content") or "").strip()
        if content.startswith("<msg") and "<img" in content:
            return
        path = message.get("ResourcePath") or message.get("ImagePath")
        if not path:
            return
        md5_value = message.get("ImageMD5")
        if not md5_value:
            md5_value = self._calculate_file_md5(Path(path)) if os.path.exists(path) else ""
            if md5_value:
                message["ImageMD5"] = md5_value
        safe_path = path.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        md5_attr = f' md5="{md5_value}"' if md5_value else ""
        rebuilt = f"<msg><img resource_path=\"{safe_path}\"{md5_attr} /></msg>"
        message["Content"] = rebuilt

    def _embed_base64_from_resource(self, message: Dict[str, Any]) -> None:
        if message.get("ImageBase64"):
            message["Content"] = message["ImageBase64"]
            return

        path = message.get("ImagePath") or message.get("ResourcePath")
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "rb") as source:
                encoded = base64.b64encode(source.read()).decode("utf-8")
            message["Content"] = encoded
            logger.debug(f"已将图片 {path} 编码为 base64 内联内容")
        except Exception as exc:
            logger.warning(f"嵌入 base64 图片失败: {exc}")

    async def process_voice_message(self, message: Dict[str, Any]):
        """处理语音消息"""
        message["Content"] = (
            message.get("Content", {})
            .get("string", "")
            .replace("\n", "")
            .replace("\t", "")
        )

        if message["FromWxid"].endswith("@chatroom"):
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info(
            "收到语音消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Content"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        if message["IsGroup"] or not message.get("ImgBuf", {}).get("buffer", ""):
            voiceurl, length = None, None
            try:
                root = ET.fromstring(message["Content"])
                voicemsg_element = root.find("voicemsg")
                if voicemsg_element is not None:
                    voiceurl = voicemsg_element.get("voiceurl")
                    length = int(voicemsg_element.get("length"))
            except Exception as e:
                logger.error("解析语音消息失败: {}, 内容: {}", e, message["Content"])
                return

            if voiceurl and length:
                silk_base64 = await self.bot.download_voice(
                    message["MsgId"], voiceurl, length
                )
                message["Content"] = await self.bot.silk_base64_to_wav_byte(silk_base64)
        else:
            silk_base64 = message.get("ImgBuf", {}).get("buffer", "")
            message["Content"] = await self.bot.silk_base64_to_wav_byte(silk_base64)

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("voice_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_emoji_message(self, message: Dict[str, Any]):
        """处理表情消息"""
        message["Content"] = (
            message.get("Content", {})
            .get("string", "")
            .replace("\n", "")
            .replace("\t", "")
        )

        if message["FromWxid"].endswith("@chatroom"):  # 群聊消息
            message["IsGroup"] = True
            split_content = message["Content"].split(":\n", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["ActualUserWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["ActualUserWxid"] = self.wxid
        else:
            message["ActualUserWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info(
            "收到表情消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["ActualUserWxid"],
            message["Content"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["ActualUserWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        # 检查是否需要处理该消息（群聊唤醒词检查）
        should_process = await self.check_group_wakeup_word(message)

        if should_process and self.ignore_check(
            message["FromWxid"], message["ActualUserWxid"]
        ):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("emoji_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_xml_message(self, message: Dict[str, Any]):
        """处理xml消息"""
        message["Content"] = (
            message.get("Content", {})
            .get("string", "")
            .replace("\n", "")
            .replace("\t", "")
        )

        if message["FromWxid"].endswith("@chatroom"):
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        # 保存消息到数据库（即使解析失败也保存）
        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        try:
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            if appmsg is None:
                logger.warning("XML 中未找到 appmsg 节点，内容: {}", message["Content"])
                return
            type_element = appmsg.find("type")
            if type_element is None:
                logger.warning("XML 中未找到 type 节点，内容: {}", message["Content"])
                return
            type_value = int(type_element.text)
            logger.debug(
                "解析到的 XML 类型: {}, 完整内容: {}", type_value, message["Content"]
            )
        except ET.ParseError as e:
            logger.error("解析 XML 失败: {}, 完整内容: {}", e, message["Content"])
            return
        except Exception as e:
            logger.error("处理 XML 时发生异常: {}, 完整内容: {}", e, message["Content"])
            return

        # 调试：打印原始消息结构（视频/语音引用）
        if message.get("MsgType") == 49:
            try:
                root = ET.fromstring(message["Content"])
                appmsg = root.find("appmsg")
                if appmsg is not None:
                    refermsg = appmsg.find("refermsg")
                    if refermsg is not None:
                        ref_type = refermsg.find("type")
                        ref_content = refermsg.find("content")
                        logger.info("RAW_QUOTE type={} refer_content={}", int(ref_type.text) if ref_type is not None and ref_type.text else -1, ref_content.text if ref_content is not None else "N/A")
            except Exception as e:
                logger.warning("解析引用消息原始结构失败: {}", e)
        if type_value == 57:  # 引用消息
            await self.process_quote_message(message)
        elif type_value == 6:  # 文件消息
            # 先触发 xml_message 事件，再处理文件消息
            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    logger.debug(
                        "触发文件消息的 xml_message 事件: 消息ID: {}",
                        message.get("MsgId", ""),
                    )
                    await EventManager.emit("xml_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")

            # 然后处理文件消息
            await self.process_file_message(message)
        elif type_value == 5:  # 公众号文章或链接分享消息
            logger.info(
                "收到链接分享消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
                message.get("MsgId", ""),
                message["FromWxid"],
                message["SenderWxid"],
                message["Content"],
            )
            logger.debug("完整 XML 内容: {}", message["Content"])
            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    logger.debug(
                        "触发 article_message 事件: 消息ID: {}",
                        message.get("MsgId", ""),
                    )
                    await EventManager.emit("article_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")
        elif type_value == 74:  # 文件消息，但还在上传
            logger.debug(
                "收到上传中文件消息: 消息ID:{} 来自:{}",
                message.get("MsgId", ""),
                message["FromWxid"],
            )
        else:
            logger.info(
                "未知的 XML 消息类型: {}, 完整内容: {}", type_value, message["Content"]
            )

        # 触发 xml_message 事件，无论 XML 类型如何
        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                logger.debug(
                    "触发 xml_message 事件: 消息ID: {}", message.get("MsgId", "")
                )
                await EventManager.emit("xml_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_quote_message(self, message: Dict[str, Any]):
        """处理引用消息"""
        quote_message = {}
        # 保存原始 XML，用于后续提取 cdnvideourl/aeskey
        original_content = message.get("Content", "")
        try:
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            text = appmsg.find("title").text
            refermsg = appmsg.find("refermsg")

            quote_message["MsgType"] = int(refermsg.find("type").text)

            if quote_message["MsgType"] == 1:  # 文本消息
                quote_message["NewMsgId"] = refermsg.find("svrid").text
                quote_message["ToWxid"] = refermsg.find("fromusr").text
                quote_message["FromWxid"] = refermsg.find("chatusr").text
                quote_message["Nickname"] = refermsg.find("displayname").text
                quote_message["MsgSource"] = refermsg.find("msgsource").text
                quote_message["Content"] = refermsg.find("content").text
                quote_message["Createtime"] = refermsg.find("createtime").text

            elif quote_message["MsgType"] == 49:  # 引用消息
                quote_message["NewMsgId"] = refermsg.find("svrid").text
                quote_message["ToWxid"] = refermsg.find("fromusr").text
                quote_message["FromWxid"] = refermsg.find("chatusr").text
                quote_message["Nickname"] = refermsg.find("displayname").text
                quote_message["MsgSource"] = refermsg.find("msgsource").text
                quote_message["Createtime"] = refermsg.find("createtime").text

                quote_message["Content"] = refermsg.find("content").text

                quote_root = ET.fromstring(quote_message["Content"])
                quote_appmsg = quote_root.find("appmsg")

                quote_message["Content"] = (
                    quote_appmsg.find("title").text
                    if isinstance(quote_appmsg.find("title"), ET.Element)
                    else ""
                )
                quote_message["destination"] = (
                    quote_appmsg.find("des").text
                    if isinstance(quote_appmsg.find("des"), ET.Element)
                    else ""
                )
                quote_message["action"] = (
                    quote_appmsg.find("action").text
                    if isinstance(quote_appmsg.find("action"), ET.Element)
                    else ""
                )
                quote_message["XmlType"] = (
                    int(quote_appmsg.find("type").text)
                    if isinstance(quote_appmsg.find("type"), ET.Element)
                    else 0
                )
                quote_message["showtype"] = (
                    int(quote_appmsg.find("showtype").text)
                    if isinstance(quote_appmsg.find("showtype"), ET.Element)
                    else 0
                )
                quote_message["soundtype"] = (
                    int(quote_appmsg.find("soundtype").text)
                    if isinstance(quote_appmsg.find("soundtype"), ET.Element)
                    else 0
                )
                quote_message["url"] = (
                    quote_appmsg.find("url").text
                    if isinstance(quote_appmsg.find("url"), ET.Element)
                    else ""
                )
                quote_message["lowurl"] = (
                    quote_appmsg.find("lowurl").text
                    if isinstance(quote_appmsg.find("lowurl"), ET.Element)
                    else ""
                )
                quote_message["dataurl"] = (
                    quote_appmsg.find("dataurl").text
                    if isinstance(quote_appmsg.find("dataurl"), ET.Element)
                    else ""
                )
                quote_message["lowdataurl"] = (
                    quote_appmsg.find("lowdataurl").text
                    if isinstance(quote_appmsg.find("lowdataurl"), ET.Element)
                    else ""
                )
                quote_message["songlyric"] = (
                    quote_appmsg.find("songlyric").text
                    if isinstance(quote_appmsg.find("songlyric"), ET.Element)
                    else ""
                )
                quote_appattach = quote_appmsg.find("appattach") if isinstance(quote_appmsg, ET.Element) else None
                quote_message["appattach"] = {}

                def _appattach_text(tag: str, default_value: str = "") -> str:
                    if not isinstance(quote_appattach, ET.Element):
                        return default_value
                    element = quote_appattach.find(tag)
                    if not isinstance(element, ET.Element) or element.text is None:
                        return default_value
                    return element.text

                def _appattach_int(tag: str, default_value: int = 0) -> int:
                    raw = _appattach_text(tag, "")
                    try:
                        return int(raw) if raw != "" else default_value
                    except Exception:
                        return default_value

                quote_message["appattach"]["totallen"] = _appattach_int("totallen", 0)
                quote_message["appattach"]["attachid"] = _appattach_text("attachid", "")
                quote_message["appattach"]["emoticonmd5"] = _appattach_text("emoticonmd5", "")
                quote_message["appattach"]["fileext"] = _appattach_text("fileext", "")
                quote_message["appattach"]["cdnthumbaeskey"] = _appattach_text("cdnthumbaeskey", "")
                quote_message["appattach"]["aeskey"] = _appattach_text("aeskey", "")
                quote_message["extinfo"] = (
                    quote_appmsg.find("extinfo").text
                    if isinstance(quote_appmsg.find("extinfo"), ET.Element)
                    else ""
                )
                quote_message["sourceusername"] = (
                    quote_appmsg.find("sourceusername").text
                    if isinstance(quote_appmsg.find("sourceusername"), ET.Element)
                    else ""
                )
                quote_message["sourcedisplayname"] = (
                    quote_appmsg.find("sourcedisplayname").text
                    if isinstance(quote_appmsg.find("sourcedisplayname"), ET.Element)
                    else ""
                )
                quote_message["thumburl"] = (
                    quote_appmsg.find("thumburl").text
                    if isinstance(quote_appmsg.find("thumburl"), ET.Element)
                    else ""
                )
                quote_message["md5"] = (
                    quote_appmsg.find("md5").text
                    if isinstance(quote_appmsg.find("md5"), ET.Element)
                    else ""
                )
                quote_message["statextstr"] = (
                    quote_appmsg.find("statextstr").text
                    if isinstance(quote_appmsg.find("statextstr"), ET.Element)
                    else ""
                )
                quote_message["directshare"] = (
                    int(quote_appmsg.find("directshare").text)
                    if isinstance(quote_appmsg.find("directshare"), ET.Element)
                    else 0
                )

            elif (
                quote_message["MsgType"] == 3
            ):  # 处理引用图片，以这个cdnthumbaeskey为图片缓存的唯一标识，方便后续在dow插件里根据cdnthumbaeskey获取到相应的图片
                quote_message["NewMsgId"] = refermsg.find("svrid").text
                quote_message["ToWxid"] = refermsg.find("fromusr").text
                quote_message["FromWxid"] = refermsg.find("chatusr").text
                quote_message["Nickname"] = refermsg.find("displayname").text
                quote_message["MsgSource"] = refermsg.find("msgsource").text
                quote_message["Content"] = refermsg.find("content").text
                quote_message["Createtime"] = refermsg.find("createtime").text

                quote_root = refermsg.find("content").text
                unescaped_inner_xml = html.unescape(quote_root)
                match = re.search(r'cdnthumbaeskey="([^"]+)"', unescaped_inner_xml)
                if match:
                    cdnthumbaeskey = match.group(1)
                    logger.debug(f"cdnthumbaeskey: {cdnthumbaeskey}")
                    quote_message["cdnthumbaeskey"] = cdnthumbaeskey
                else:
                    logger.debug("cdnthumbaeskey not found.")
                    quote_message["cdnthumbaeskey"] = ""
                # 提取完整 aeskey 和 CDN 地址，用于下载高清图片
                match_aes = re.search(r'aeskey="([^"]+)"', unescaped_inner_xml)
                match_url = re.search(r'cdnmidimgurl="([^"]+)"', unescaped_inner_xml) or re.search(r'cdnbigimgurl="([^"]+)"', unescaped_inner_xml)
                match_md5 = re.search(r'md5="([^"]+)"', unescaped_inner_xml)
                if match_url:
                    quote_message["cdnmidimgurl"] = match_url.group(1)
                if match_aes:
                    quote_message["aeskey"] = match_aes.group(1)
                if match_md5:
                    quote_message["ImageMD5"] = match_md5.group(1)

            elif quote_message["MsgType"] in (43, 34):  # 视频/语音引用
                quote_message["NewMsgId"] = refermsg.find("svrid").text
                quote_message["ToWxid"] = refermsg.find("fromusr").text
                quote_message["FromWxid"] = refermsg.find("chatusr").text
                quote_message["Nickname"] = refermsg.find("displayname").text
                quote_message["MsgSource"] = refermsg.find("msgsource").text
                content_text = refermsg.find("content").text
                quote_message["Content"] = content_text
                quote_message["Createtime"] = refermsg.find("createtime").text
                quote_message["RawReferMsgXml"] = ET.tostring(refermsg, encoding="unicode", method="xml")

                # 从原始 XML 的 appmsg.videomsg 提取视频字段（cdnvideourl/aeskey/md5等）
                # 如果 refermsg content 里没有 CDN，用 createtime 查数据库找原始视频消息
                if not quote_message.get("cdnurl") and quote_message.get("Createtime"):
                    try:
                        import sqlite3
                        createtime = int(quote_message["Createtime"])
                        import datetime
                        ts = datetime.datetime.fromtimestamp(createtime)
                        conn = sqlite3.connect("/app/database/message.db")
                        c = conn.cursor()
                        # 查 ±60 秒内的 type=43 消息
                        start_ts = (ts - datetime.timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
                        end_ts = (ts + datetime.timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
                        c.execute(
                            "SELECT content FROM messages WHERE msg_type=43 AND timestamp BETWEEN ? AND ? ORDER BY timestamp ASC LIMIT 1",
                            (start_ts, end_ts),
                        )
                        row = c.fetchone()
                        conn.close()
                        if row and row[0]:
                            import re
                            m = re.search(r'cdnvideourl="([^"]+)"', row[0])
                            if m:
                                quote_message["cdnurl"] = m.group(1)
                            m = re.search(r'aeskey="([^"]+)"', row[0])
                            if m:
                                quote_message["aeskey"] = m.group(1)
                            m = re.search(r'md5="([^"]+)"', row[0])
                            if m:
                                quote_message["md5"] = m.group(1)
                            m = re.search(r'length="([^"]+)"', row[0])
                            if m:
                                quote_message["length"] = m.group(1)
                    except Exception:
                        pass

        except Exception as e:
            logger.error("解析引用消息失败: {}, 完整内容: {}", e, message["Content"])
            return

        message["Content"] = text
        message["Quote"] = quote_message

        # 引用消息同样可能包含 atuserlist（例如：@机器人后再引用）。统一解析并填充 Ats。
        # 仅依赖 MsgSource，不强行从 Content 猜测，避免误判。
        try:
            msg_source_root = ET.fromstring(message.get("MsgSource", ""))
            at_list = (
                msg_source_root.find("atuserlist").text
                if msg_source_root.find("atuserlist") is not None
                else ""
            )
        except Exception:
            at_list = ""

        parsed_ats: list[str]
        if at_list:
            parsed_ats = [item for item in at_list.strip(",").split(",") if item]
        else:
            parsed_ats = []
        if parsed_ats:
            message["Ats"] = parsed_ats
        else:
            message.setdefault("Ats", [])

        logger.info(
            "收到引用消息: 消息ID:{} 来自:{} 发送人:{} 内容:{} 引用:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Content"],
            message["Quote"],
        )

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                # 在发送给插件之前，先检查引用消息中的触发词/唤醒词
                logger.info(f"开始检查引用消息触发词: {message.get('Content', '')}")
                try:
                    if await self.check_quote_message_triggers(message):
                        # 如果触发词检查已经处理了消息，就不再发送给其他插件
                        logger.info("引用消息已被触发词处理，跳过通用引用消息处理")
                        return
                    else:
                        logger.info("引用消息未匹配到任何触发词，继续通用处理")
                except Exception as e:
                    logger.error(f"检查引用消息触发词时发生异常: {e}")

                # 如果没有触发词匹配，继续正常的引用消息处理流程
                await EventManager.emit("quote_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def check_quote_message_triggers(self, message: Dict[str, Any]) -> bool:
        """检查引用消息中的触发词/唤醒词，并调用相应插件处理

        Args:
            message: 引用消息字典

        Returns:
            bool: 如果消息被某个插件处理了，返回True；否则返回False
        """
        from utils.plugin_manager import plugin_manager

        content = message.get("Content", "").strip()
        if not content:
            return False

        content_lower = content.lower()
        logger.debug(f"检查引用消息中的触发词: {content}")

        def normalize_token(token: Any) -> Optional[str]:
            if isinstance(token, str):
                value = token.strip()
                return value or None
            return None

        # 遍历所有已加载的插件，按优先级排序
        plugins_by_priority = {}
        for plugin_name, plugin in plugin_manager.plugins.items():
            # 获取插件的优先级
            priority = 50  # 默认优先级

            # 检查插件是否有处理引用消息的方法
            for method_name in dir(plugin):
                method = getattr(plugin, method_name)
                if hasattr(method, "_event_type") and method._event_type == "quote_message":
                    # 如果有，获取其优先级
                    priority = getattr(method, "_priority", 50)
                    break

            # 将插件添加到对应优先级的列表中
            if priority not in plugins_by_priority:
                plugins_by_priority[priority] = []
            plugins_by_priority[priority].append((plugin_name, plugin))

        # 按优先级从高到低排序
        priorities = sorted(plugins_by_priority.keys(), reverse=True)

        # 检查每个插件的触发词/唤醒词
        for priority in priorities:
            for plugin_name, plugin in plugins_by_priority[priority]:
                # 1. 检查插件是否有唤醒词属性
                if hasattr(plugin, "wakeup_words") and plugin.wakeup_words:
                    for wakeup_word in plugin.wakeup_words:
                        normalized = normalize_token(wakeup_word)
                        if not normalized:
                            continue
                        wake_lower = normalized.lower()
                        if content_lower.startswith(wake_lower) or f" {wake_lower}" in content_lower:
                            logger.info(f"引用消息中检测到插件 {plugin_name} 的唤醒词: {normalized}")
                            # 触发该插件的引用消息处理方法
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if hasattr(method, "_event_type") and method._event_type == "quote_message":
                                    result = await method(self.bot, message)
                                    if result is False:
                                        return True
                                    break

                # 2. 检查Dify插件的特殊处理
                if (plugin_name == "Dify" and hasattr(plugin, "wakeup_word_to_model")
                    and plugin.wakeup_word_to_model):
                    for wakeup_word in plugin.wakeup_word_to_model.keys():
                        normalized = normalize_token(wakeup_word)
                        if not normalized:
                            continue
                        wake_lower = normalized.lower()
                        if content_lower.startswith(wake_lower) or f" {wake_lower}" in content_lower:
                            logger.info(f"引用消息中检测到Dify插件的唤醒词: {normalized}")

                            # 为Dify插件特殊处理：直接调用文本消息处理方法
                            # 创建一个修改后的消息副本，构建包含引用信息的查询
                            modified_message = message.copy()

                            # 构建包含引用信息的查询内容
                            quote_info = message.get("Quote", {})
                            quoted_content = quote_info.get("Content", "")
                            quoted_msg_type = quote_info.get("MsgType")

                            # 根据引用消息类型构建查询
                            if quoted_msg_type == 3:  # 图片消息
                                query_content = f"{content} (基于引用的图片)"
                            elif quoted_msg_type == 43:  # 视频消息
                                query_content = f"{content} (基于引用的视频)"
                            elif quoted_msg_type == 49:  # 文档/文件消息
                                query_content = f"{content} (基于引用的文档/文件)"
                            elif quoted_content and quoted_content.strip():  # 有文本内容的消息
                                query_content = f"{content} (引用消息: '{quoted_content}')"
                            else:  # 其他类型或无内容的消息
                                query_content = f"{content} (基于引用的消息)"

                            modified_message["Content"] = query_content
                            logger.info(f"为Dify插件构建引用查询: {query_content}")

                            # 触发Dify插件的文本消息处理方法
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if hasattr(method, "_event_type") and method._event_type == "text_message":
                                    result = await method(self.bot, modified_message)
                                    if result is False:
                                        return True
                                    break

                # 3. 检查插件的触发词属性
                if hasattr(plugin, "trigger_words") and plugin.trigger_words:
                    for trigger_word in plugin.trigger_words:
                        normalized = normalize_token(trigger_word)
                        if not normalized:
                            continue
                        if normalized.lower() in content_lower:
                            logger.info(f"引用消息中检测到插件 {plugin_name} 的触发词: {normalized}")
                            # 触发该插件的引用消息处理方法
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if hasattr(method, "_event_type") and method._event_type == "quote_message":
                                    result = await method(self.bot, message)
                                    if result is False:
                                        return True
                                    break

                # 4. 检查插件的commands属性
                if hasattr(plugin, "commands") and plugin.commands:
                    for command in plugin.commands:
                        normalized = normalize_token(command)
                        if not normalized:
                            continue
                        command_lower = normalized.lower()
                        content_first_word = content_lower.split(" ", 1)[0]
                        if content_lower.startswith(command_lower) or content_first_word == command_lower:
                            logger.info(f"引用消息中检测到插件 {plugin_name} 的命令: {normalized}")
                            # 触发该插件的引用消息处理方法
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if hasattr(method, "_event_type") and method._event_type == "quote_message":
                                    result = await method(self.bot, message)
                                    if result is False:
                                        return True
                                    break

        return False

    async def process_video_message(self, message):
        message["Content"] = message.get("Content", {}).get("string", "")

        if message["FromWxid"].endswith("@chatroom"):
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        logger.info(
            "收到视频消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Content"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        message["Video"] = await self.bot.download_video(message.get("MsgId", 0))

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("video_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_file_message(self, message: Dict[str, Any]):
        """处理文件消息"""
        try:
            root = ET.fromstring(message["Content"])
            appmsg = root.find("appmsg")
            appattach = appmsg.find("appattach") if appmsg is not None else None
            title_node = appmsg.find("title") if appmsg is not None else None
            attach_node = appattach.find("attachid") if appattach is not None else None
            ext_node = appattach.find("fileext") if appattach is not None else None

            filename = (title_node.text or "").strip() if title_node is not None and title_node.text else ""
            attach_id = (attach_node.text or "").strip() if attach_node is not None and attach_node.text else ""
            file_extend = (ext_node.text or "").strip() if ext_node is not None and ext_node.text else ""
            if not file_extend and filename:
                file_extend = Path(filename).suffix.lstrip(".")
            if not filename or not attach_id:
                raise ValueError("缺少文件名或 attach_id")
        except Exception as e:
            logger.error("解析文件消息失败: {}, 内容: {}", e, message["Content"])
            return

        message["Filename"] = filename
        message["FileExtend"] = file_extend

        logger.info(
            "收到文件消息: 消息ID:{} 来自:{} 发送人:{} XML:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Content"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=message["Content"],
            is_group=message["IsGroup"],
        )

        message["File"] = await self.bot.download_attach(attach_id)

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("file_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_system_message(self, message: Dict[str, Any]):
        """处理系统消息"""
        message["Content"] = message.get("Content", {}).get("string", "")

        if message["FromWxid"].endswith("@chatroom"):
            message["IsGroup"] = True
            split_content = message["Content"].split(":", 1)
            if len(split_content) > 1:
                message["Content"] = split_content[1]
                message["SenderWxid"] = split_content[0]

                # 不更新群聊中发送者的联系人信息
                # 根据需求，只更新群聊本身信息，不更新群聊中发送者信息
            else:
                message["Content"] = split_content[0]
                message["SenderWxid"] = self.wxid
        else:
            message["SenderWxid"] = message["FromWxid"]
            if message["FromWxid"] == self.wxid:
                message["FromWxid"] = message["ToWxid"]
            message["IsGroup"] = False

        try:
            root = ET.fromstring(message["Content"])
            msg_type = root.attrib["type"]
        except Exception as e:
            logger.error("解析系统消息失败: {}, 内容: {}", e, message["Content"])
            return

        if msg_type == "pat":
            await self.process_pat_message(message)
        elif msg_type == "ClientCheckGetExtInfo":
            pass
        else:
            logger.info("收到系统消息: {}, 完整内容: {}", message, message["Content"])
            if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
                if self.ignore_protection or not protector.check(14400):
                    await EventManager.emit("system_message", self.bot, message)
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def process_pat_message(self, message: Dict[str, Any]):
        """处理拍一拍请求消息"""
        try:
            root = ET.fromstring(message["Content"])
            pat = root.find("pat")
            patter = pat.find("fromusername").text
            patted = pat.find("pattedusername").text
            pat_suffix = pat.find("patsuffix").text
        except Exception as e:
            logger.error("解析拍一拍消息失败: {}, 内容: {}", e, message["Content"])
            return

        message["Patter"] = patter
        message["Patted"] = patted
        message["PatSuffix"] = pat_suffix

        logger.info(
            "收到拍一拍消息: 消息ID:{} 来自:{} 发送人:{} 拍者:{} 被拍:{} 后缀:{}",
            message.get("MsgId", ""),
            message["FromWxid"],
            message["SenderWxid"],
            message["Patter"],
            message["Patted"],
            message["PatSuffix"],
        )

        await self.msg_db.save_message(
            msg_id=int(message.get("MsgId", 0)),
            sender_wxid=message["SenderWxid"],
            from_wxid=message["FromWxid"],
            msg_type=int(message.get("MsgType", 0)),
            content=f"{message['Patter']} 拍了拍 {message['Patted']} {message['PatSuffix']}",
            is_group=message["IsGroup"],
        )

        if self.ignore_check(message["FromWxid"], message["SenderWxid"]):
            if self.ignore_protection or not protector.check(14400):
                await EventManager.emit("pat_message", self.bot, message)
            else:
                logger.warning("风控保护: 新设备登录后4小时内请挂机")

    async def check_wakeup_words(self, message: Dict[str, Any]) -> bool:
        """检查消息是否包含任何插件的唤醒词或触发词

        Args:
            message: 消息字典

        Returns:
            bool: 如果消息包含唤醒词或触发词并且已经被处理，返回True；否则返回False
        """
        from utils.plugin_manager import plugin_manager

        content = message.get("Content", "").strip()
        if not content:
            return False

        bot_wxid = (getattr(self.bot, "wxid", None) or self.wxid or "").strip()

        # 移除@部分，以便正确匹配唤醒词或触发词
        # 检查消息是否包含Ats字段，并且机器人的wxid在Ats列表中
        if bot_wxid and "Ats" in message and bot_wxid in message["Ats"]:
            # 尝试从消息内容中移除@部分
            # 从main_config.toml中读取机器人名称列表
            robot_names = []
            try:
                # 读取main_config.toml中的配置
                import tomllib

                with open("main_config.toml", "rb") as f:
                    main_config = tomllib.load(f)
                    robot_names = main_config.get("XYBot", {}).get("robot-names", [])
                    logger.debug(
                        f"从main_config.toml中读取到机器人名称列表: {robot_names}"
                    )
            except Exception as e:
                logger.error(f"读取main_config.toml中的机器人名称失败: {e}")

            # 如果配置文件中没有设置或读取失败，使用默认值
            if not robot_names:
                robot_names = ["小小x", "小x", "机器人"]
                logger.debug(f"使用默认机器人名称列表: {robot_names}")

            # 添加机器人自己的昵称
            if self.nickname and self.nickname not in robot_names:
                robot_names.append(self.nickname)

            # 尝试从群成员列表中获取机器人的群昵称
            if message["FromWxid"].endswith("@chatroom"):
                try:
                    # 异步获取群成员列表
                    members = await self.get_chatroom_member_list(message["FromWxid"])
                    for member in members:
                        if member.get("wxid") == bot_wxid and member.get("nickname"):
                            robot_names.append(member["nickname"])
                            logger.debug(
                                f"从群成员列表中获取到机器人的群昵称: {member['nickname']}"
                            )
                            break
                except Exception as e:
                    logger.warning(f"获取群成员列表失败: {e}")

            # 移除@机器人前缀
            original_content = content
            for robot_name in robot_names:
                at_prefix = f"@{robot_name}"
                if content.startswith(at_prefix):
                    content = content[len(at_prefix) :].strip()
                    logger.debug(f"移除@{robot_name}后的查询内容: {content}")
                    break

            # 如果没有找到匹配的机器人名称，尝试使用正则表达式移除@部分
            if content == original_content:
                import re

                # 匹配开头的@xxx部分
                at_pattern = r"^@[^\s]+"
                match = re.search(at_pattern, content)
                if match:
                    at_part = match.group(0)
                    content = content[len(at_part) :].strip()
                    logger.debug(
                        f"使用正则表达式移除@部分: {at_part}，剩余内容: {content}"
                    )

        # 如果内容为空，则不处理
        if not content:
            return False

        # 保存原始消息内容，以便在处理完成后恢复
        original_message_content = message["Content"]
        # 更新消息内容为处理后的内容，以便插件处理
        message["Content"] = content

        try:
            # 遍历所有已加载的插件，按优先级排序
            plugins_by_priority = {}
            for plugin_name, plugin in plugin_manager.plugins.items():
                # 获取插件的优先级
                priority = 50  # 默认优先级

                # 检查插件是否有处理@消息的方法
                for method_name in dir(plugin):
                    method = getattr(plugin, method_name)
                    if (
                        hasattr(method, "_event_type")
                        and method._event_type == "at_message"
                    ):
                        # 如果有，获取其优先级
                        priority = getattr(method, "_priority", 50)
                        break

                # 将插件添加到对应优先级的列表中
                if priority not in plugins_by_priority:
                    plugins_by_priority[priority] = []
                plugins_by_priority[priority].append((plugin_name, plugin))

            # 按优先级从高到低排序
            priorities = sorted(plugins_by_priority.keys(), reverse=True)

            # 检查每个插件的唤醒词
            for priority in priorities:
                for plugin_name, plugin in plugins_by_priority[priority]:
                    # 1. 检查插件是否有唤醒词属性
                    if hasattr(plugin, "wakeup_words") and plugin.wakeup_words:
                        for wakeup_word in plugin.wakeup_words:
                            if wakeup_word.lower() in content.lower():
                                logger.info(
                                    f"检测到插件 {plugin_name} 的唤醒词: {wakeup_word}"
                                )
                                # 触发该插件的at_message事件
                                for method_name in dir(plugin):
                                    method = getattr(plugin, method_name)
                                    if (
                                        hasattr(method, "_event_type")
                                        and method._event_type == "at_message"
                                    ):
                                        # 调用插件的at_message处理方法
                                        result = await method(self.bot, message)
                                        # 如果插件返回False，表示阻止后续处理
                                        if result is False:
                                            return True
                                        break

                    # 2. 检查Dify插件的特殊处理
                    if (
                        plugin_name == "Dify"
                        and hasattr(plugin, "wakeup_word_to_model")
                        and plugin.wakeup_word_to_model
                    ):
                        for wakeup_word in plugin.wakeup_word_to_model.keys():
                            content_lower = content.lower()
                            wakeup_lower = wakeup_word.lower()
                            if (
                                content_lower.startswith(wakeup_lower)
                                or f" {wakeup_lower}" in content_lower
                            ):
                                logger.info(f"检测到Dify插件的唤醒词: {wakeup_word}")
                                # 触发Dify插件的at_message事件
                                for method_name in dir(plugin):
                                    method = getattr(plugin, method_name)
                                    if (
                                        hasattr(method, "_event_type")
                                        and method._event_type == "at_message"
                                    ):
                                        # 调用插件的at_message处理方法
                                        result = await method(self.bot, message)
                                        # 如果插件返回False，表示阻止后续处理
                                        if result is False:
                                            return True
                                        break

                    # 3. 检查插件的触发词属性（如YujieSajiao插件的trigger_words）
                    if hasattr(plugin, "trigger_words") and plugin.trigger_words:
                        for trigger_word in plugin.trigger_words:
                            if trigger_word.lower() in content.lower():
                                logger.info(
                                    f"检测到插件 {plugin_name} 的触发词: {trigger_word}"
                                )

                                # 检查插件是否有处理文本消息的方法
                                text_message_method = None
                                for method_name in dir(plugin):
                                    method = getattr(plugin, method_name)
                                    if (
                                        hasattr(method, "_event_type")
                                        and method._event_type == "text_message"
                                    ):
                                        text_message_method = method
                                        break

                                if text_message_method:
                                    # 创建一个临时消息对象，模拟文本消息
                                    temp_message = message.copy()
                                    # 使用处理后的内容（移除了@部分）
                                    temp_message["Content"] = content

                                    # 调用插件的text_message处理方法
                                    result = await text_message_method(
                                        self.bot, temp_message
                                    )

                                    # 如果插件返回False，表示阻止后续处理
                                    if result is False:
                                        return True
                                break

                    # 4. 检查插件的commands属性（常见于AI对话插件）
                    if hasattr(plugin, "commands") and plugin.commands:
                        for command in plugin.commands:
                            # 检查两种情况：
                            # 1. 内容以命令开头（前缀匹配）
                            # 2. 内容的第一个词与命令完全匹配（完全匹配，用于FastGPT等插件）
                            content_first_word = content.split(" ", 1)[0].lower()
                            if (
                                content.lower().startswith(command.lower())
                                or content_first_word == command.lower()
                            ):
                                logger.info(
                                    f"检测到插件 {plugin_name} 的命令: {command}"
                                )

                                # 检查插件是否有处理文本消息的方法
                                text_message_method = None
                                for method_name in dir(plugin):
                                    method = getattr(plugin, method_name)
                                    if (
                                        hasattr(method, "_event_type")
                                        and method._event_type == "text_message"
                                    ):
                                        text_message_method = method
                                        break

                                if text_message_method:
                                    # 创建一个临时消息对象，模拟文本消息
                                    temp_message = message.copy()
                                    # 使用处理后的内容（移除了@部分）
                                    temp_message["Content"] = content

                                    # 调用插件的text_message处理方法
                                    result = await text_message_method(
                                        self.bot, temp_message
                                    )

                                    # 如果插件返回False，表示阻止后续处理
                                    if result is False:
                                        return True
                                break

                    # 4.1 检查插件的command属性（单数形式，如VideoDemand插件）
                    if hasattr(plugin, "command") and plugin.command:
                        # 处理列表类型的command
                        if isinstance(plugin.command, list):
                            for cmd in plugin.command:
                                if (
                                    isinstance(cmd, str)
                                    and content.lower() == cmd.lower()
                                ):
                                    logger.info(
                                        f"检测到插件 {plugin_name} 的命令(单数形式): {cmd}"
                                    )

                                    # 检查插件是否有处理文本消息的方法
                                    text_message_method = None
                                    for method_name in dir(plugin):
                                        method = getattr(plugin, method_name)
                                        if (
                                            hasattr(method, "_event_type")
                                            and method._event_type == "text_message"
                                        ):
                                            text_message_method = method
                                            break

                                    if text_message_method:
                                        # 创建一个临时消息对象，模拟文本消息
                                        temp_message = message.copy()
                                        # 使用处理后的内容（移除了@部分）
                                        temp_message["Content"] = content

                                        # 调用插件的text_message处理方法
                                        result = await text_message_method(
                                            self.bot, temp_message
                                        )

                                        # 如果插件返回False，表示阻止后续处理
                                        if result is False:
                                            return True
                                    break
                        # 处理字符串类型的command
                        elif (
                            isinstance(plugin.command, str)
                            and content.lower() == plugin.command.lower()
                        ):
                            logger.info(
                                f"检测到插件 {plugin_name} 的命令(单数形式): {plugin.command}"
                            )

                            # 检查插件是否有处理文本消息的方法
                            text_message_method = None
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if (
                                    hasattr(method, "_event_type")
                                    and method._event_type == "text_message"
                                ):
                                    text_message_method = method
                                    break

                            if text_message_method:
                                # 创建一个临时消息对象，模拟文本消息
                                temp_message = message.copy()
                                # 使用处理后的内容（移除了@部分）
                                temp_message["Content"] = content

                                # 调用插件的text_message处理方法
                                result = await text_message_method(
                                    self.bot, temp_message
                                )

                                # 如果插件返回False，表示阻止后续处理
                                if result is False:
                                    return True

                    # 5. 检查插件的所有可能的command属性
                    # 自动检测插件的所有属性，查找可能的命令
                    for attr_name in dir(plugin):
                        # 只检查包含'command'的属性名，并且不是方法或内置属性
                        if (
                            "command" in attr_name.lower()
                            and not attr_name.startswith("__")
                            and not callable(getattr(plugin, attr_name))
                        ):
                            command_value = getattr(plugin, attr_name)

                            # 处理字符串类型的命令
                            if (
                                isinstance(command_value, str)
                                and content.lower() == command_value.lower()
                            ):
                                logger.info(
                                    f"检测到插件 {plugin_name} 的命令: {command_value}"
                                )

                                # 检查插件是否有处理文本消息的方法
                                text_message_method = None
                                for method_name in dir(plugin):
                                    method = getattr(plugin, method_name)
                                    if (
                                        hasattr(method, "_event_type")
                                        and method._event_type == "text_message"
                                    ):
                                        text_message_method = method
                                        break

                                if text_message_method:
                                    # 创建一个临时消息对象，模拟文本消息
                                    temp_message = message.copy()
                                    # 使用处理后的内容（移除了@部分）
                                    temp_message["Content"] = content

                                    # 调用插件的text_message处理方法
                                    result = await text_message_method(
                                        self.bot, temp_message
                                    )

                                    # 如果插件返回False，表示阻止后续处理
                                    if result is False:
                                        return True
                                break

                            # 处理列表类型的命令
                            elif isinstance(command_value, list):
                                for cmd in command_value:
                                    if (
                                        isinstance(cmd, str)
                                        and content.lower() == cmd.lower()
                                    ):
                                        logger.info(
                                            f"检测到插件 {plugin_name} 的命令: {cmd}"
                                        )

                                        # 检查插件是否有处理文本消息的方法
                                        text_message_method = None
                                        for method_name in dir(plugin):
                                            method = getattr(plugin, method_name)
                                            if (
                                                hasattr(method, "_event_type")
                                                and method._event_type == "text_message"
                                            ):
                                                text_message_method = method
                                                break

                                        if text_message_method:
                                            # 创建一个临时消息对象，模拟文本消息
                                            temp_message = message.copy()
                                            # 使用处理后的内容（移除了@部分）
                                            temp_message["Content"] = content

                                            # 调用插件的text_message处理方法
                                            result = await text_message_method(
                                                self.bot, temp_message
                                            )

                                            # 如果插件返回False，表示阻止后续处理
                                            if result is False:
                                                return True
                                        break

                    # 6. 检查插件的command_prefix属性（如DifyConversationManager插件的command_prefix）
                    if hasattr(plugin, "command_prefix") and isinstance(
                        plugin.command_prefix, str
                    ):
                        prefix = plugin.command_prefix
                        if content.startswith(prefix):
                            logger.info(
                                f"检测到插件 {plugin_name} 的命令前缀: {prefix}"
                            )

                            # 检查插件是否有处理文本消息的方法
                            text_message_method = None
                            for method_name in dir(plugin):
                                method = getattr(plugin, method_name)
                                if (
                                    hasattr(method, "_event_type")
                                    and method._event_type == "text_message"
                                ):
                                    text_message_method = method
                                    break

                            if text_message_method:
                                # 创建一个临时消息对象，模拟文本消息
                                temp_message = message.copy()
                                # 使用处理后的内容（移除了@部分）
                                temp_message["Content"] = content

                                # 调用插件的text_message处理方法
                                result = await text_message_method(
                                    self.bot, temp_message
                                )

                                # 如果插件返回False，表示阻止后续处理
                                if result is False:
                                    return True

                    # 6. 通用处理：检查插件的at_message方法
                    # 许多插件没有明确定义命令属性，而是在处理方法中直接检查命令
                    # 我们直接调用插件的at_message方法，让插件自己判断是否处理该消息
                    for method_name in dir(plugin):
                        method = getattr(plugin, method_name)
                        if (
                            hasattr(method, "_event_type")
                            and method._event_type == "at_message"
                        ):
                            # 调用插件的at_message处理方法
                            result = await method(self.bot, message)
                            # 如果插件返回False，表示它处理了消息并阻止后续处理
                            if result is False:
                                logger.info(f"插件 {plugin_name} 处理了@消息")
                                return True
                            # 如果插件返回True，表示它没有处理消息，继续检查其他插件
                            break
        finally:
            # 恢复原始消息内容
            message["Content"] = original_message_content

        return False

    async def check_group_wakeup_word(self, message: Dict[str, Any]) -> bool:
        """检查群聊消息是否包含唤醒词

        Args:
            message: 消息字典

        Returns:
            bool: 如果消息应该被进一步处理返回True，否则返回False
        """
        # 如果不是群聊消息或者未启用群聊唤醒词功能，直接返回True（继续处理）
        if not message.get("IsGroup", False) or not self.enable_group_wakeup:
            return True

        content = message.get("Content", "").strip()
        bot_wxid = (getattr(self.bot, "wxid", None) or self.wxid or "").strip()
        # 检查消息是否以任一唤醒词开头
        for wakeup_word in self.group_wakeup_words:
            if content.lower().startswith(wakeup_word.lower()):
                # 移除唤醒词，保留实际命令内容
                message["OriginalContent"] = message["Content"]
                message["Content"] = content[len(wakeup_word) :].strip()
                logger.info(
                    f"检测到群聊唤醒词: {wakeup_word}, 处理后内容: {message['Content']}"
                )

                # 将机器人的wxid添加到Ats列表中，模拟@机器人的效果
                if bot_wxid and bot_wxid not in message.get("Ats", []):
                    message["Ats"] = message.get("Ats", []) + [bot_wxid]
                    logger.debug(
                        f"将机器人wxid {bot_wxid} 添加到Ats列表中，模拟@机器人效果"
                    )

                # 尝试找到并调用Dify插件处理该消息
                from utils.event_manager import EventManager  # 导入事件管理器
                from utils.plugin_manager import plugin_manager

                # 创建一个临时消息对象，避免修改原消息
                temp_message = message.copy()

                # 直接触发事件系统的text_message事件，让所有插件处理该消息
                # 这将确保消息只被处理一次
                if self.ignore_protection or not protector.check(14400):
                    # 异步触发事件，但不等待结果
                    asyncio.create_task(
                        EventManager.emit("text_message", self.bot, temp_message)
                    )
                else:
                    logger.warning("风控保护: 新设备登录后4小时内请挂机")

                # 返回False，表示消息已经被处理，不需要继续处理
                return False

        # 没有唤醒词，返回True让消息继续传递给处理链
        return True

    def ignore_check(self, FromWxid: str, SenderWxid: str):
        # 过滤公众号消息（公众号wxid通常以gh_开头）
        if SenderWxid and isinstance(SenderWxid, str) and SenderWxid.startswith("gh_"):
            logger.debug(f"忽略公众号消息: {SenderWxid}")
            return False
        if FromWxid and isinstance(FromWxid, str) and FromWxid.startswith("gh_"):
            logger.debug(f"忽略公众号消息: {FromWxid}")
            return False

        # 过滤微信团队和系统通知
        system_accounts = [
            "weixin",  # 微信团队
            "filehelper",  # 文件传输助手
            "fmessage",  # 朋友推荐通知
            "medianote",  # 语音记事本
            "floatbottle",  # 漂流瓶
            "qmessage",  # QQ离线消息
            "qqmail",  # QQ邮箱提醒
            "tmessage",  # 腾讯新闻
            "weibo",  # 微博推送
            "newsapp",  # 新闻推送
            "notification_messages",  # 服务通知
            "helper_entry",  # 新版微信运动
            "mphelper",  # 公众号助手
            "brandsessionholder",  # 公众号消息
            "weixinreminder",  # 微信提醒
            "officialaccounts",  # 公众平台
        ]

        # 检查是否是系统账号
        for account in system_accounts:
            if (
                SenderWxid and isinstance(SenderWxid, str) and SenderWxid == account
            ) or (FromWxid and isinstance(FromWxid, str) and FromWxid == account):
                logger.debug(f"忽略系统账号消息: {SenderWxid or FromWxid}")
                return False

        # 检测其他特殊账号特征
        # 微信支付相关通知
        if (SenderWxid and isinstance(SenderWxid, str) and "wxpay" in SenderWxid) or (
            FromWxid and isinstance(FromWxid, str) and "wxpay" in FromWxid
        ):
            logger.debug(f"忽略微信支付相关消息: {SenderWxid or FromWxid}")
            return False

        # 腾讯游戏相关通知
        if (
            SenderWxid
            and isinstance(SenderWxid, str)
            and ("tencent" in SenderWxid.lower() or "game" in SenderWxid.lower())
        ) or (
            FromWxid
            and isinstance(FromWxid, str)
            and ("tencent" in FromWxid.lower() or "game" in FromWxid.lower())
        ):
            logger.debug(f"忽略腾讯游戏相关消息: {SenderWxid or FromWxid}")
            return False

        # 其他特殊账号特征
        # 微信官方账号通常包含"service"或"official"
        if (
            SenderWxid
            and isinstance(SenderWxid, str)
            and ("service" in SenderWxid.lower() or "official" in SenderWxid.lower())
        ) or (
            FromWxid
            and isinstance(FromWxid, str)
            and ("service" in FromWxid.lower() or "official" in FromWxid.lower())
        ):
            logger.debug(f"忽略官方服务账号消息: {SenderWxid or FromWxid}")
            return False

        # 先检查是否是群聊消息
        is_group = (
            FromWxid and isinstance(FromWxid, str) and FromWxid.endswith("@chatroom")
        )

        if self.ignore_mode == "Whitelist":
            if is_group:
                # 群聊消息：有两种情况
                # 1. 群聊ID在白名单中（处理该群中的所有消息）
                # 2. 发送者ID在白名单中（无论群聊ID是否在白名单中）

                # 当发送者ID在白名单中，或者群聊ID在白名单中时，才处理消息
                # 修改逻辑，允许处理白名单群聊中的所有消息，而不仅仅是机器人自己发送的消息
                logger.debug(
                    f"白名单检查: 群聊ID={FromWxid}, 发送者ID={SenderWxid}, 群聊ID在白名单中={FromWxid in self.whitelist}, 发送者ID在白名单中={SenderWxid in self.whitelist}"
                )
                return SenderWxid in self.whitelist or FromWxid in self.whitelist
            else:
                # 私聊消息：发送者ID在白名单中
                return SenderWxid in self.whitelist
        elif self.ignore_mode == "Blacklist":
            if is_group:
                # 群聊消息：群聊ID不在黑名单中且发送者ID不在黑名单中
                return (FromWxid not in self.blacklist) and (
                    SenderWxid not in self.blacklist
                )
            else:
                # 私聊消息：发送者ID不在黑名单中
                return SenderWxid not in self.blacklist
        else:
            # 默认处理所有消息
            return True

    # 朋友圈相关方法
    async def get_friend_circle_list(self, max_id: int = 0) -> dict:
        """获取自己的朋友圈列表

        Args:
            max_id: 朋友圈ID，用于分页获取

        Returns:
            dict: 朋友圈数据
        """
        return await self.bot.get_pyq_list(self.wxid, max_id)

    async def get_user_friend_circle(self, wxid: str, max_id: int = 0) -> dict:
        """获取特定用户的朋友圈

        Args:
            wxid: 用户wxid
            max_id: 朋友圈ID，用于分页获取

        Returns:
            dict: 朋友圈数据
        """
        return await self.bot.get_pyq_detail(wxid=self.wxid, Towxid=wxid, max_id=max_id)

    async def like_friend_circle(self, id: str) -> dict:
        """点赞朋友圈

        Args:
            id: 朋友圈ID

        Returns:
            dict: 点赞结果
        """
        return await self.bot.put_pyq_comment(wxid=self.wxid, id=id, type=1)

    async def comment_friend_circle(self, id: str, content: str) -> dict:
        """评论朋友圈

        Args:
            id: 朋友圈ID
            content: 评论内容

        Returns:
            dict: 评论结果
        """
        return await self.bot.put_pyq_comment(
            wxid=self.wxid, id=id, Content=content, type=2
        )
