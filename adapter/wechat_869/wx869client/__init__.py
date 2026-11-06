"""869 协议客户端（归属适配器，主线不再内置）。

@input: 869 网关 HTTP/WS、会话文件
@output: WechatAPIClient（869 实现，供 adapter/wechat_869 使用）
@position: adapter/wechat_869 私有客户端包
"""
import os
import sqlite3

from loguru import logger

from WechatAPI.Client.base import WechatAPIClientBase, Proxy, Section
from WechatAPI.errors import *
from .core import WechatAPIClient as _WechatAPIClient

__all__ = ["WechatAPIClient", "WechatAPIClientBase", "Proxy", "Section"]


class WechatAPIClient(_WechatAPIClient):
    """869 客户端（含 contacts.db 本地昵称等兼容能力）。"""

    def get_contacts_db(self):
        if getattr(self, "contacts_db", None) is None:
            try:
                if os.path.exists("/app/database"):
                    db_path = "/app/database/contacts.db"
                else:
                    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    db_path = os.path.join(base_dir, "database", "contacts.db")
                self.contacts_db = sqlite3.connect(db_path)
                logger.info(f"联系人数据库初始化成功: {db_path}")
            except Exception as e:
                logger.error(f"初始化联系人数据库失败: {str(e)}")
                self.contacts_db = None
        return self.contacts_db

    def get_local_nickname(self, wxid: str, chatroom_id: str = None):
        if not wxid:
            return None
        if chatroom_id and "@chatroom" in chatroom_id:
            try:
                conn = self.get_contacts_db()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT member_wxid, nickname, display_name FROM group_members "
                        "WHERE group_wxid = ? AND member_wxid = ?",
                        (chatroom_id, wxid),
                    )
                    result = cursor.fetchone()
                    if result:
                        if result[2]:
                            return result[2]
                        elif result[1]:
                            return result[1]
            except Exception as e:
                logger.error(f"从contacts.db获取昵称失败: {str(e)}")
        return None

    def __del__(self):
        if hasattr(self, "contacts_db") and self.contacts_db:
            try:
                self.contacts_db.close()
            except Exception:
                pass
