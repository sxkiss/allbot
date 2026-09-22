"""
@input: status 字符串、details、extra_data；admin.server.set_bot_instance
@output: 写入 admin/bot_status.json 与根 bot_status.json、同步 bot 实例到管理后台（保留登录态）
@position: bot_core 全局运行状态管理层（管理后台状态文件与实例桥接）
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import json
import sys
import time
from pathlib import Path

from loguru import logger


# 初始化管理后台集成
admin_path = str(Path(__file__).resolve().parent.parent)
if admin_path not in sys.path:
    sys.path.append(admin_path)

# 导入管理后台服务器模块
try:
    from admin.server import set_bot_instance as admin_set_bot_instance
    logger.debug("成功导入admin.server.set_bot_instance")
except ImportError as e:
    logger.error(f"导入admin.server.set_bot_instance失败: {e}")

    def admin_set_bot_instance(bot):
        logger.warning("admin.server.set_bot_instance未导入，调用被忽略")
        return None


def update_bot_status(status, details=None, extra_data=None):
    """更新bot状态，供管理后台读取

    Args:
        status: 状态字符串
        details: 详细信息
        extra_data: 额外数据字典
    """
    try:
        # 使用统一的路径写入状态文件
        status_file = Path(admin_path) / "admin" / "bot_status.json"
        root_status_file = Path(admin_path) / "bot_status.json"

        # 读取当前状态
        current_status = {}
        if status_file.exists():
            with open(status_file, "r", encoding="utf-8") as f:
                current_status = json.load(f)

        # 更新状态
        current_status["status"] = status
        current_status["timestamp"] = time.time()
        if details:
            current_status["details"] = details

        # 添加额外数据
        if extra_data and isinstance(extra_data, dict):
            for key, value in extra_data.items():
                current_status[key] = value

        # 确保目录存在
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # 写入status_file
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(current_status, f)

        # 写入root_status_file
        with open(root_status_file, "w", encoding="utf-8") as f:
            json.dump(current_status, f)

        logger.debug(f"成功更新bot状态: {status}, 路径: {status_file} 和 {root_status_file}")

        # 输出更多调试信息
        if "nickname" in current_status:
            logger.debug(f"状态文件包含昵称: {current_status['nickname']}")
        if "wxid" in current_status:
            logger.debug(f"状态文件包含微信ID: {current_status['wxid']}")
        if "alias" in current_status:
            logger.debug(f"状态文件包含微信号: {current_status['alias']}")

    except Exception as e:
        logger.error(f"更新bot状态失败: {e}")


def set_bot_instance(bot):
    """设置bot实例到管理后台

    Args:
        bot: AllBot 实例

    Returns:
        bot 实例
    """
    # 先调用admin模块的设置函数
    admin_set_bot_instance(bot)

    # 更新状态：不要覆盖登录成功后写入的 online / 等待登录时的 waiting_login 等真实登录态。
    # 启动时 set_bot_instance 与后台 869 登录任务并发执行，若登录已先完成并写入 online，
    # 此处若再写 initialized 会把在线态覆盖成“未登录”，导致后台误报离线。
    current_status = ""
    try:
        sf = Path(admin_path) / "admin" / "bot_status.json"
        if sf.exists():
            with open(sf, "r", encoding="utf-8") as f:
                current_status = str(json.load(f).get("status", "") or "").strip().lower()
    except Exception:
        current_status = ""

    if current_status in {"online", "ready", "success", "waiting_login", "scanning", "adapter_mode"}:
        # 保留真实登录态，仅刷新时间戳
        update_bot_status(current_status, "机器人实例已设置（保留登录态）")
    else:
        update_bot_status("initialized", "机器人实例已设置")

    logger.success("成功设置bot实例并更新状态")

    return bot
