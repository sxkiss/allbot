"""
@input: WechatAPI 客户端、main_config.toml（协议前缀兜底）、contacts_db 持久层
@output: 联系人/群成员标准化结果与联系人信息更新写库
@position: AllBot 联系人域服务（屏蔽协议差异，向上层提供统一联系人能力）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import json
import tomllib
from typing import List, Dict, Any

import aiohttp
from loguru import logger

from database.contacts_db import get_contact_from_db, update_contact_in_db
from WechatAPI import WechatAPIClient


class ContactManager:
    """联系人管理器"""

    def __init__(self, bot_client: WechatAPIClient):
        self.bot = bot_client

    async def get_chatroom_member_list(self, group_wxid: str) -> List[Dict[str, Any]]:
        """获取群成员列表

        Args:
            group_wxid: 群聊的wxid

        Returns:
            群成员列表
        """
        if not group_wxid.endswith("@chatroom"):
            logger.error(f"无效的群ID: {group_wxid}，只有群聊才能获取成员列表")
            return []

        # 优先走客户端封装方法（自动适配大小写与 key 参数）
        if hasattr(self.bot, "get_chatroom_member_list"):
            try:
                members = await self.bot.get_chatroom_member_list(group_wxid)
                if isinstance(members, list):
                    logger.info(f"通过客户端方法获取群 {group_wxid} 成员列表成功，共 {len(members)} 个成员")
                    return self._normalize_members(members)
            except Exception as e:
                logger.warning(f"客户端方法获取群 {group_wxid} 成员列表失败，将回退直接HTTP调用: {e}")

        try:
            logger.info(f"开始获取群 {group_wxid} 的成员列表")

            # 获取微信API的基本配置
            api_base = "http://127.0.0.1:9011"
            if hasattr(self.bot, "ip") and hasattr(self.bot, "port"):
                api_base = f"http://{self.bot.ip}:{self.bot.port}"

            # 确定API路径前缀
            api_prefix = self._get_api_prefix()

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
                    logger.error(f"获取群成员列表失败: HTTP状态码 {response.status}")
                    return []

                # 解析响应数据
                try:
                    json_resp = await response.json()
                    logger.info(f"收到API响应: {json.dumps(json_resp)[:200]}...")

                    if json_resp.get("Success"):
                        members_data = self._extract_members_data(json_resp)
                        logger.info(
                            f"成功获取群 {group_wxid} 的成员列表，共 {len(members_data)} 个成员"
                        )
                        return self._normalize_members(members_data)
                    else:
                        error_msg = (
                            json_resp.get("Message")
                            or json_resp.get("message")
                            or "未知错误"
                        )
                        logger.warning(f"获取群 {group_wxid} 成员列表失败: {error_msg}")
                        return []
                except Exception as e:
                    logger.error(f"解析群成员响应数据失败: {str(e)}")
                    return []
        except Exception as e:
            logger.error(f"获取群成员列表时发生异常: {str(e)}")
            return []

    def _get_api_prefix(self) -> str:
        """获取API路径前缀"""
        # 先检查是否有显式设置的前缀
        if hasattr(self.bot, "api_prefix"):
            return self.bot.api_prefix
        elif hasattr(self.bot, "_api_prefix"):
            return self.bot._api_prefix

        # 如果没有显式设置，则根据协议版本确定
        try:
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)
                protocol_version = config.get("Protocol", {}).get("version", "849")

                # 根据协议版本选择前缀
                if protocol_version == "849":
                    api_prefix = "/VXAPI"
                    logger.info(f"使用849协议前缀: {api_prefix}")
                else:  # 855 或 ipad
                    api_prefix = "/api"
                    logger.info(f"使用{protocol_version}协议前缀: {api_prefix}")
                return api_prefix
        except Exception as e:
            logger.warning(f"读取协议版本失败，使用默认前缀: {e}")
            return "/VXAPI"

    def _extract_members_data(self, json_resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从API响应中提取成员数据"""
        members_data = []

        # 根据实际响应结构提取成员列表
        if (
            json_resp.get("Data")
            and json_resp["Data"].get("NewChatroomData")
            and json_resp["Data"]["NewChatroomData"].get("ChatRoomMember")
        ):
            members_data = json_resp["Data"]["NewChatroomData"]["ChatRoomMember"]
            logger.info(
                f"从 NewChatroomData.ChatRoomMember 获取到 {len(members_data)} 个成员"
            )
        elif json_resp.get("Data") and json_resp["Data"].get("ChatRoomMember"):
            members_data = json_resp["Data"]["ChatRoomMember"]
            logger.info(f"从 Data.ChatRoomMember 获取到 {len(members_data)} 个成员")
        elif json_resp.get("Data") and isinstance(json_resp["Data"], list):
            members_data = json_resp["Data"]
            logger.info(f"从 Data 数组获取到 {len(members_data)} 个成员")

        return members_data

    def _normalize_members(self, members_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """标准化成员信息，确保每个成员都有基本字段"""
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
                    member["nickname"] = member.get("wxid", "Unknown")

            # 处理头像字段
            if not member.get("avatar"):
                if member.get("BigHeadImgUrl"):
                    member["avatar"] = member["BigHeadImgUrl"]
                elif member.get("SmallHeadImgUrl"):
                    member["avatar"] = member["SmallHeadImgUrl"]

            members.append(member)

        return members

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
                # 如果是群聊，不获取详细信息
                if wxid.endswith("@chatroom"):
                    contact_info = {"wxid": wxid, "nickname": wxid, "type": "group"}
                    update_contact_in_db(contact_info)
                    logger.debug(f"已在消息处理中更新群聊 {wxid} 的基本信息")
                else:
                    # 获取联系人详细信息
                    await self._fetch_and_update_contact(wxid)
        except Exception as e:
            logger.error(f"更新联系人信息时发生异常: {str(e)}")

    async def _fetch_and_update_contact(self, wxid: str):
        """从API获取并更新联系人信息"""
        try:
            logger.debug(f"开始获取联系人 {wxid} 的详细信息")
            detail = await self.bot.get_contract_detail(wxid)
            logger.debug(f"获取到联系人 {wxid} 的详细信息: {detail}")

            if detail:
                contact_info = self._parse_contact_detail(wxid, detail)
                update_contact_in_db(contact_info)
                logger.debug(f"已在消息处理中更新联系人 {wxid} 的信息")
            else:
                logger.warning(f"无法获取联系人 {wxid} 的详细信息，API返回空数据")
                self._save_basic_contact(wxid, "friend")
        except Exception as e:
            logger.error(f"调用API获取联系人 {wxid} 详情失败: {str(e)}")
            self._save_basic_contact(wxid, "friend")

    def _parse_contact_detail(self, wxid: str, detail: Any) -> Dict[str, Any]:
        """解析联系人详情"""
        # 处理列表格式
        if isinstance(detail, list) and len(detail) > 0:
            detail_item = detail[0]
            if isinstance(detail_item, dict):
                return self._extract_contact_fields(wxid, detail_item)
            else:
                logger.warning(f"联系人 {wxid} 详情格式不是字典: {detail_item}")
                return {"wxid": wxid, "nickname": wxid, "type": "friend"}

        # 处理字典格式
        elif isinstance(detail, dict):
            return self._extract_contact_fields(wxid, detail)

        else:
            logger.warning(f"联系人 {wxid} 详情格式不支持: {type(detail)}")
            return {"wxid": wxid, "nickname": wxid, "type": "friend"}

    def _extract_contact_fields(self, wxid: str, detail_item: Dict[str, Any]) -> Dict[str, Any]:
        """从详情字典中提取联系人字段"""
        logger.debug(f"联系人 {wxid} 详情字段: {list(detail_item.keys())}")

        # 提取昵称
        nickname_value = self._extract_field_value(
            detail_item, ["nickname", "NickName"], wxid
        )

        # 提取头像
        avatar_value = self._extract_avatar(detail_item)

        # 提取备注
        remark_value = self._extract_field_value(detail_item, ["remark", "Remark"], "")

        # 提取微信号
        alias_value = self._extract_field_value(detail_item, ["alias", "Alias"], "")

        return {
            "wxid": wxid,
            "nickname": nickname_value,
            "avatar": avatar_value,
            "remark": remark_value,
            "alias": alias_value,
        }

    def _extract_field_value(
        self, detail_item: Dict[str, Any], field_names: List[str], default: str
    ) -> str:
        """提取字段值，支持多个字段名和字典嵌套"""
        for field_name in field_names:
            value = detail_item.get(field_name)
            if value is not None:
                # 如果是字典类型，尝试获取其中的string字段
                if isinstance(value, dict):
                    string_value = value.get("string")
                    if string_value:
                        return string_value
                elif value:  # 非空字符串
                    return value
        return default

    def _extract_avatar(self, detail_item: Dict[str, Any]) -> str:
        """提取头像URL"""
        # 优先使用BigHeadImgUrl或SmallHeadImgUrl
        avatar_value = detail_item.get("BigHeadImgUrl", "")
        if not avatar_value:
            avatar_value = detail_item.get("SmallHeadImgUrl", "")
        if not avatar_value:
            # 如果没有直接的URL，尝试使用avatar字段
            avatar_value = detail_item.get("avatar", "")
            if isinstance(avatar_value, dict):
                avatar_value = avatar_value.get("string", "")
        return avatar_value

    def _save_basic_contact(self, wxid: str, contact_type: str):
        """保存基本联系人信息"""
        contact_info = {
            "wxid": wxid,
            "nickname": wxid,
            "type": contact_type,
        }
        update_contact_in_db(contact_info)
        logger.debug(f"已在消息处理中更新联系人 {wxid} 的基本信息")
