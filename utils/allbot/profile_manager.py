"""个人信息管理器模块

负责管理机器人的个人信息（wxid、昵称等）
"""
from typing import Optional


class ProfileManager:
    """个人信息管理器"""

    def __init__(self):
        self.wxid: Optional[str] = None
        self.nickname: Optional[str] = None
        self.alias: Optional[str] = None
        self.phone: Optional[str] = None

    def update(self, wxid: str, nickname: str, alias: str, phone: str):
        """更新机器人信息

        Args:
            wxid: 微信ID
            nickname: 昵称
            alias: 别名
            phone: 手机号
        """
        self.wxid = wxid
        self.nickname = nickname
        self.alias = alias
        self.phone = phone

    def is_logged_in(self) -> bool:
        """检查机器人是否已登录

        Returns:
            如果已登录返回True，否则返回False
        """
        return self.wxid is not None

    def get_wxid(self) -> Optional[str]:
        """获取微信ID"""
        return self.wxid

    def get_nickname(self) -> Optional[str]:
        """获取昵称"""
        return self.nickname

    def get_alias(self) -> Optional[str]:
        """获取别名"""
        return self.alias

    def get_phone(self) -> Optional[str]:
        """获取手机号"""
        return self.phone
