"""朋友圈管理器模块

负责朋友圈相关功能（获取列表、点赞、评论等）
"""
from typing import Dict, Any

from WechatAPI import WechatAPIClient


class FriendCircleManager:
    """朋友圈管理器"""

    def __init__(self, bot_client: WechatAPIClient, profile_manager):
        """初始化朋友圈管理器

        Args:
            bot_client: 微信API客户端
            profile_manager: 个人信息管理器
        """
        self.bot = bot_client
        self.profile_manager = profile_manager

    async def get_friend_circle_list(self, max_id: int = 0) -> Dict[str, Any]:
        """获取自己的朋友圈列表

        Args:
            max_id: 朋友圈ID，用于分页获取

        Returns:
            朋友圈数据
        """
        wxid = self.profile_manager.get_wxid()
        return await self.bot.get_pyq_list(wxid, max_id)

    async def get_user_friend_circle(self, wxid: str, max_id: int = 0) -> Dict[str, Any]:
        """获取特定用户的朋友圈

        Args:
            wxid: 用户wxid
            max_id: 朋友圈ID，用于分页获取

        Returns:
            朋友圈数据
        """
        my_wxid = self.profile_manager.get_wxid()
        return await self.bot.get_pyq_detail(wxid=my_wxid, Towxid=wxid, max_id=max_id)

    async def like_friend_circle(self, id: str) -> Dict[str, Any]:
        """点赞朋友圈

        Args:
            id: 朋友圈ID

        Returns:
            点赞结果
        """
        wxid = self.profile_manager.get_wxid()
        return await self.bot.put_pyq_comment(wxid=wxid, id=id, type=1)

    async def comment_friend_circle(self, id: str, content: str) -> Dict[str, Any]:
        """评论朋友圈

        Args:
            id: 朋友圈ID
            content: 评论内容

        Returns:
            评论结果
        """
        wxid = self.profile_manager.get_wxid()
        return await self.bot.put_pyq_comment(wxid=wxid, id=id, Content=content, type=2)
