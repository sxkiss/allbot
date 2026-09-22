"""权限检查器模块

负责检查消息是否应该被处理（白名单/黑名单/系统账号过滤）
"""
from typing import List

from loguru import logger


class PermissionChecker:
    """权限检查器"""

    def __init__(self, ignore_mode: str, whitelist: List[str], blacklist: List[str]):
        """初始化权限检查器

        Args:
            ignore_mode: 过滤模式（None/Whitelist/Blacklist）
            whitelist: 白名单列表
            blacklist: 黑名单列表
        """
        self.ignore_mode = ignore_mode
        self.whitelist = whitelist
        self.blacklist = blacklist

        logger.info(f"消息过滤模式: {self.ignore_mode}")
        logger.info(f"白名单: {self.whitelist}")
        logger.info(f"黑名单: {self.blacklist}")

    def ignore_check(self, from_wxid: str, sender_wxid: str) -> bool:
        """检查消息是否应该被处理

        Args:
            from_wxid: 消息来源wxid（群聊ID或私聊对方ID）
            sender_wxid: 发送者wxid

        Returns:
            True表示应该处理，False表示应该忽略
        """
        # 过滤公众号消息（公众号wxid通常以gh_开头）
        if self._is_official_account(sender_wxid) or self._is_official_account(from_wxid):
            return False

        # 过滤系统账号
        if self._is_system_account(sender_wxid) or self._is_system_account(from_wxid):
            return False

        # 检测其他特殊账号特征
        if self._is_special_account(sender_wxid) or self._is_special_account(from_wxid):
            return False

        # 先检查是否是群聊消息
        is_group = from_wxid and isinstance(from_wxid, str) and from_wxid.endswith("@chatroom")

        if self.ignore_mode == "Whitelist":
            if is_group:
                # 群聊消息：发送者ID在白名单中，或者群聊ID在白名单中
                logger.debug(
                    f"白名单检查: 群聊ID={from_wxid}, 发送者ID={sender_wxid}, "
                    f"群聊ID在白名单中={from_wxid in self.whitelist}, "
                    f"发送者ID在白名单中={sender_wxid in self.whitelist}"
                )
                return sender_wxid in self.whitelist or from_wxid in self.whitelist
            else:
                # 私聊消息：发送者ID在白名单中
                return sender_wxid in self.whitelist

        elif self.ignore_mode == "Blacklist":
            if is_group:
                # 群聊消息：群聊ID不在黑名单中且发送者ID不在黑名单中
                return (from_wxid not in self.blacklist) and (sender_wxid not in self.blacklist)
            else:
                # 私聊消息：发送者ID不在黑名单中
                return sender_wxid not in self.blacklist
        else:
            # 默认处理所有消息
            return True

    def _is_official_account(self, wxid: str) -> bool:
        """检查是否是公众号"""
        if wxid and isinstance(wxid, str) and wxid.startswith("gh_"):
            logger.debug(f"忽略公众号消息: {wxid}")
            return True
        return False

    def _is_system_account(self, wxid: str) -> bool:
        """检查是否是系统账号"""
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

        if wxid and isinstance(wxid, str) and wxid in system_accounts:
            logger.debug(f"忽略系统账号消息: {wxid}")
            return True
        return False

    def _is_special_account(self, wxid: str) -> bool:
        """检查是否是特殊账号（微信支付、腾讯游戏、官方服务等）"""
        if not wxid or not isinstance(wxid, str):
            return False

        # 微信支付相关通知
        if "wxpay" in wxid:
            logger.debug(f"忽略微信支付相关消息: {wxid}")
            return True

        # 腾讯游戏相关通知
        if "tencent" in wxid.lower() or "game" in wxid.lower():
            logger.debug(f"忽略腾讯游戏相关消息: {wxid}")
            return True

        # 微信官方账号通常包含"service"或"official"
        if "service" in wxid.lower() or "official" in wxid.lower():
            logger.debug(f"忽略官方服务账号消息: {wxid}")
            return True

        return False
