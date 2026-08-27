"""
@input: WechatAPIClient 统一微信客户端实现（以网关协议为主）
@output: WechatAPIClient（供框架/插件调用，保留原有方法签名）
@position: Client 聚合入口（Facade），单一客户端实现
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from WechatAPI.errors import *
from .base import WechatAPIClientBase, Proxy, Section
from .core import WechatAPIClient
from .protect import protector
import sqlite3
import os
from loguru import logger

__all__ = ["WechatAPIClient", "WechatAPIClientBase", "Proxy", "Section"]


class WechatAPIClient(WechatAPIClient):
    """兼容别名：保持 WechatAPIClient 名称与插件调用方式不变。"""

    def get_contacts_db(self):
        """连接到contacts.db数据库"""
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
        """从本地contacts.db获取用户昵称"""
        if not wxid:
            return None

        if chatroom_id and "@chatroom" in chatroom_id:
            try:
                conn = self.get_contacts_db()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT member_wxid, nickname, display_name FROM group_members
                        WHERE group_wxid = ? AND member_wxid = ?
                        """,
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
        """清理资源"""
        if hasattr(self, "contacts_db") and self.contacts_db:
            try:
                self.contacts_db.close()
            except Exception:
                pass
