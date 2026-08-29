"""
@input: bot 实例（需支持 get_chatroom_member_list）、插件配置 config.toml、sqlite3 本地库
@output: 周期性更新群成员表并在检测退群时发送提醒
@position: 插件 - 群成员监控（退群提醒）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import asyncio
import sqlite3
import os
import tomllib
from datetime import datetime
from typing import List, Dict

from utils.plugin_base import PluginBase
from loguru import logger


class GroupMonitorPlugin(PluginBase):
    description = "退群提醒插件"
    author = "BEelzebub"
    version = "1.1.0"

    def __init__(self):
        super().__init__()
        self._monitor_task = None

        # 读取配置文件
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        with open(config_path, "rb") as f:
            self.config = tomllib.load(f)

        # 确保使用绝对路径创建数据库
        db_file = self.config["Config"]["Database"]["path"]
        self.db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), db_file))

        # 基本配置
        self.check_interval = self.config["Config"]["check_interval"]
        self.monitor_groups = self.config["Config"]["monitor_groups"]
        self.message_template = self.config["Config"]["message_template"]
        self.debug = self.config["Config"]["debug"]

        # 卡片消息配置
        self.use_card = self.config["Config"]["Card"]["enable"]
        self.card_title_template = self.config["Config"]["Card"]["title_template"]
        self.card_description_template = self.config["Config"]["Card"]["description_template"]
        self.card_url = self.config["Config"]["Card"]["url"]

        # 初始化数据库
        self.init_db()

        # 记录是否是首次运行
        self.is_first_run = True

    def init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='group_members'")
        table_exists = cursor.fetchone() is not None

        if not table_exists:
            # 创建新表，包含头像URL字段
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                group_id TEXT,
                member_id TEXT,
                member_name TEXT,
                avatar_url TEXT,
                last_seen TEXT,
                PRIMARY KEY (group_id, member_id)
            )
            """)
        else:
            # 检查是否需要添加avatar_url列
            cursor.execute("PRAGMA table_info(group_members)")
            columns = [column[1] for column in cursor.fetchall()]
            if "avatar_url" not in columns:
                cursor.execute("ALTER TABLE group_members ADD COLUMN avatar_url TEXT")
                logger.info("已向数据库表添加avatar_url列")

        conn.commit()
        conn.close()

    async def get_member_avatar(self, group_id: str, member_id: str) -> str:
        """获取群成员头像URL"""
        try:
            if not self.bot or not hasattr(self.bot, "get_chatroom_member_list"):
                return ""

            group_members = await self.bot.get_chatroom_member_list(group_id)
            if isinstance(group_members, list) and group_members:
                for member_data in group_members:
                    member_wxid = member_data.get("UserName") or member_data.get("Wxid") or member_data.get("wxid") or ""
                    if member_wxid == member_id:
                        avatar_url = member_data.get("BigHeadImgUrl") or member_data.get("SmallHeadImgUrl") or ""
                        logger.debug(f"成功获取到群成员 {member_id} 的头像地址: {avatar_url}")
                        return avatar_url
        except Exception as e:
            logger.error(f"获取群成员头像失败: {e}")

        return ""  # 如果获取失败，返回空字符串

    async def update_members(self, group_id: str, members: List[Dict]):
        """更新群成员数据并检测退出成员"""
        current_time = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # 获取当前数据库中的成员
            cursor.execute("SELECT member_id, member_name, avatar_url FROM group_members WHERE group_id = ?", (group_id,))
            old_members_data = {row[0]: {"name": row[1], "avatar": row[2]} for row in cursor.fetchall()}
            old_members = set(old_members_data.keys())

            if self.debug:
                logger.debug(f"群 {group_id} 当前数据库中的成员数: {len(old_members)}")

            # 获取新的成员列表
            new_members = set()

            for member in members:
                member_id = member.get("UserName")
                if not member_id:
                    logger.warning(f"成员数据中缺少Wxid: {member}")
                    continue
                new_members.add(member_id)
                member_name = member.get("NickName", "未知用户")

                # 获取头像URL
                avatar_url = member.get("BigHeadImgUrl") or member.get("SmallHeadImgUrl") or ""

                # 更新或插入成员信息，包含头像URL
                cursor.execute("""
                INSERT OR REPLACE INTO group_members (group_id, member_id, member_name, avatar_url, last_seen)
                VALUES (?, ?, ?, ?, ?)
                """, (group_id, member_id, member_name, avatar_url, current_time))

            # 首次运行时不发送通知，只记录成员
            if self.is_first_run and not old_members:
                logger.info(f"首次运行，记录群 {group_id} 的 {len(new_members)} 名成员")
                conn.commit()
                return []

            # 检查退出的成员
            left_members = old_members - new_members
            left_member_info = []

            for member_id in left_members:
                member_data = old_members_data.get(member_id, {"name": "未知用户", "avatar": ""})
                member_name = member_data["name"]
                avatar_url = member_data["avatar"] or ""  # 使用存储的头像URL

                left_member_info.append((member_id, member_name))
                logger.info(f"检测到群 {group_id} 成员退出: {member_name} ({member_id})")

                # 获取当前时间
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                try:
                    # 发送提醒消息
                    if self.use_card:
                        # 格式化卡片标题和描述
                        card_title = self.card_title_template.format(member_name=member_name)
                        card_description = self.card_description_template.format(
                            member_name=member_name,
                            member_id=member_id,
                            time=now
                        )

                        # 发送卡片消息，使用存储的头像URL
                        await self.bot.send_link_message(
                            group_id,
                            title=card_title,
                            description=card_description,
                            url=self.card_url,
                            thumb_url=avatar_url
                        )
                        logger.debug(f"已发送退群卡片提醒: {card_title}")
                        if avatar_url:
                            logger.debug(f"使用存储的头像URL: {avatar_url}")
                        else:
                            logger.debug("未找到存储的头像URL，使用空字符串")
                    else:
                        # 发送文本消息
                        remind_message = self.message_template.format(member_name=member_name, member_id=member_id)
                        await self.bot.send_text_message(group_id, remind_message)
                        logger.debug(f"已发送退群文本提醒: {remind_message}")
                except Exception as e:
                    logger.error(f"发送退群提醒消息失败: {e}")

            # 删除退出的成员记录
            for member_id in left_members:
                cursor.execute(
                    "DELETE FROM group_members WHERE group_id = ? AND member_id = ?",
                    (group_id, member_id)
                )

            conn.commit()
            logger.debug(f"群 {group_id} 成员数据更新完成，当前成员数: {len(new_members)}")
            return left_member_info

        except Exception as e:
            logger.error(f"更新群 {group_id} 成员数据时发生错误: {str(e)}")
            return []
        finally:
            conn.close()

    async def monitor_loop(self):
        """监控循环，用于定期更新群成员数据"""
        while True:
            if not self.monitor_groups:
                if self.debug:
                    logger.info("没有配置需要监控的群聊")
                await asyncio.sleep(self.check_interval)
                continue

            for group_id in self.monitor_groups:
                try:
                    if self.debug:
                        logger.info(f"正在获取群 {group_id} 的成员列表...")

                    if not self.bot or not hasattr(self.bot, "get_chatroom_member_list"):
                        logger.warning("bot 未初始化或不支持 get_chatroom_member_list，跳过群成员同步")
                        continue

                    members = await self.bot.get_chatroom_member_list(group_id)
                    if self.debug:
                        logger.info(f"成功获取群 {group_id} 的成员列表，成员数量：{len(members) if isinstance(members, list) else 0}")

                    if isinstance(members, list) and members:
                        left_members = await self.update_members(group_id, members)
                        if self.debug:
                            logger.info(f"群 {group_id} 成员数据更新完成，退群成员数：{len(left_members) if left_members else 0}")
                    else:
                        logger.warning(f"群 {group_id} 获取到的成员列表为空或格式不正确")
                except Exception as e:
                    logger.error(f"更新群 {group_id} 成员列表失败: {e}")
                    logger.error(f"错误详情: {str(e)}")

                # 每个群之间稍微暂停一下，避免API请求过于频繁
                await asyncio.sleep(1)

            # 首次运行完成后，设置标志为False
            if self.is_first_run:
                self.is_first_run = False
                logger.info("首次运行完成，已记录所有群成员信息")

            # 等待下一次检查
            await asyncio.sleep(self.check_interval)



    async def on_enable(self, bot=None):
        """插件启用时的处理"""
        await super().on_enable(bot)
        self.bot = bot

        # 检查数据库是否为空
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM group_members")
        count = cursor.fetchone()[0]
        conn.close()

        # 如果数据库为空，设置首次运行标志为True
        self.is_first_run = (count == 0)

        if self.is_first_run:
            logger.info("首次运行，将初始化群成员数据")
        else:
            logger.info(f"数据库中已有 {count} 条群成员记录")

        # 启动监控循环
        logger.info("启动群成员监控循环，每 {} 秒检查一次".format(self.check_interval))
        self._monitor_task = asyncio.create_task(self.monitor_loop())

    async def on_disable(self):
        """插件禁用时取消后台监控任务"""
        if getattr(self, "_monitor_task", None) and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("停止群成员监控任务时出现异常: {}", exc)
        self._monitor_task = None
        await super().on_disable()
