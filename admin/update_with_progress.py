"""
@input: os/asyncio/tempfile/shutil/zipfile/io/json/datetime from stdlib; requests; loguru.logger
@output: update_with_progress(version_info, update_progress_manager, get_github_url, current_dir)；更新备份仅保留最新 1 份；更新后正确写入版本号
@position: 管理后台的版本更新执行器，负责下载/备份/更新文件、校验完整性并在失败时回滚
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import os
import asyncio
import tempfile
import shutil
import zipfile
import io
import json
import requests
from datetime import datetime
from loguru import logger


def _merge_copy_tree(
    src_dir: str,
    dst_dir: str,
    *,
    excluded_names: set[str],
):
    """
    合并更新目录：递归复制 src_dir -> dst_dir，覆盖代码文件但不删除目标多余内容。

    规则：
    - 若目标已存在且文件名属于 excluded_names，则跳过覆盖（用于保留本地配置）。
    - 若目标不存在，则仍会复制（用于首次生成默认配置）。
    """
    if not os.path.isdir(src_dir):
        return

    if os.path.exists(dst_dir) and not os.path.isdir(dst_dir):
        os.remove(dst_dir)
    os.makedirs(dst_dir, exist_ok=True)
    for root, _, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        dst_root = dst_dir if rel_root == "." else os.path.join(dst_dir, rel_root)
        if os.path.exists(dst_root) and not os.path.isdir(dst_root):
            os.remove(dst_root)
        os.makedirs(dst_root, exist_ok=True)

        for filename in files:
            src_file = os.path.join(root, filename)
            dst_file = os.path.join(dst_root, filename)

            if filename in excluded_names and os.path.exists(dst_file):
                continue

            shutil.copy2(src_file, dst_file)


def _validate_update(root_dir: str) -> list[str]:
    missing: list[str] = []
    required_files = [
        "main.py",
        os.path.join("adapter", "loader.py"),
        os.path.join("adapter", "base.py"),
        os.path.join("bot_core", "orchestrator.py"),
    ]

    for rel_path in required_files:
        abs_path = os.path.join(root_dir, rel_path)
        if not os.path.isfile(abs_path):
            missing.append(rel_path)

    adapter_dir = os.path.join(root_dir, "adapter")
    if not os.path.isdir(adapter_dir):
        missing.append("adapter/")
    else:
        entries = [name for name in os.listdir(adapter_dir) if not name.startswith(".")]
        if not entries:
            missing.append("adapter/empty")

    return missing


def _restore_from_backup(backup_dir: str, root_dir: str, update_items: list[str]):
    for item in update_items:
        backup_path = os.path.join(backup_dir, item)
        if not os.path.exists(backup_path):
            continue

        dst_path = os.path.join(root_dir, item)
        if os.path.isdir(dst_path):
            shutil.rmtree(dst_path)
        elif os.path.exists(dst_path):
            os.remove(dst_path)

        if os.path.isdir(backup_path):
            shutil.copytree(backup_path, dst_path)
        else:
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(backup_path, dst_path)


def _list_update_backup_dirs(root_dir: str) -> list[str]:
    backups: list[str] = []
    try:
        for name in os.listdir(root_dir):
            path = os.path.join(root_dir, name)
            if not os.path.isdir(path):
                continue
            if not name.startswith("backup_"):
                continue
            stamp = name[len("backup_") :]
            if len(stamp) == 14 and stamp.isdigit():
                backups.append(path)
    except Exception as e:
        logger.warning(f"枚举更新备份目录失败: {e}")
    backups.sort(key=lambda p: os.path.basename(p))
    return backups


def _cleanup_old_update_backups(
    root_dir: str,
    *,
    keep: int = 1,
    keep_path: str | None = None,
) -> None:
    """只保留最近 keep 份自动更新备份。"""
    keep = max(1, int(keep or 1))
    backups = _list_update_backup_dirs(root_dir)
    if not backups:
        return

    keep_set = set()
    if keep_path:
        keep_set.add(os.path.abspath(keep_path))

    for path in backups[-keep:]:
        keep_set.add(os.path.abspath(path))

    removed = 0
    for path in backups:
        if os.path.abspath(path) in keep_set:
            continue
        try:
            shutil.rmtree(path)
            removed += 1
            logger.info(f"已清理旧更新备份: {os.path.basename(path)}")
        except Exception as e:
            logger.warning(f"清理旧更新备份失败 {path}: {e}")

    if removed:
        logger.info(f"自动更新备份清理完成，删除 {removed} 份，保留最新 {keep} 份")


async def update_with_progress(version_info: dict, update_progress_manager, get_github_url, current_dir):
    """
    带进度推送的更新流程

    Args:
        version_info: 版本信息字典
        update_progress_manager: 更新进度管理器实例
        get_github_url: GitHub URL转换函数
        current_dir: 当前目录路径
    """
    temp_dir = None
    try:
        # 开始更新
        await update_progress_manager.start_update()
        await update_progress_manager.update_progress(0, "初始化", "准备开始更新...")
        await asyncio.sleep(0.5)

        # 阶段1: 创建临时目录 (12.5%)
        await update_progress_manager.update_progress(12, "创建临时目录", "正在创建临时工作目录...")
        temp_dir = tempfile.mkdtemp(prefix="allbot_update_")
        logger.info(f"创建临时目录: {temp_dir}")
        await asyncio.sleep(0.5)

        # 阶段2: 下载更新包 (25%)
        await update_progress_manager.update_progress(25, "下载更新", "正在从GitHub下载最新版本...")
        zip_url = get_github_url("https://github.com/sxkiss/allbot/archive/refs/heads/main.zip")
        logger.info(f"正在从 {zip_url} 下载最新代码...")

        response = requests.get(zip_url, timeout=60)
        if response.status_code != 200:
            raise Exception(f"下载失败: HTTP {response.status_code}")

        # 阶段3: 解压文件 (37.5%)
        await update_progress_manager.update_progress(37, "解压文件", "正在解压更新包...")
        z = zipfile.ZipFile(io.BytesIO(response.content))
        z.extractall(temp_dir)
        logger.info(f"已解压文件到临时目录: {temp_dir}")
        await asyncio.sleep(0.5)

        # 获取解压后的目录
        extracted_dir = None
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            if os.path.isdir(item_path):
                extracted_dir = item_path
                break

        if not extracted_dir:
            raise Exception("解压后未找到有效目录")

        # 阶段4: 创建备份 (50%)
        await update_progress_manager.update_progress(50, "创建备份", "正在备份当前版本...")
        root_dir = os.path.dirname(current_dir)
        backup_dir = os.path.join(root_dir, "backup_" + datetime.now().strftime("%Y%m%d%H%M%S"))
        os.makedirs(backup_dir, exist_ok=True)
        logger.info(f"创建备份目录: {backup_dir}")
        await asyncio.sleep(0.5)

        # 需要更新的文件列表（反映最新项目结构）
        update_items = [
            "admin",                      # 管理后台（FastAPI + 模块化路由）
            "WechatAPI",                  # 微信API客户端封装
            "utils",                      # 工具模块（装饰器、事件管理、插件管理等）
            "adapter",                    # 多平台适配器（QQ/Telegram/Web/Windows）
            "plugins",                    # 插件目录（合并更新，保留本地配置与额外插件）
            "bot_core",                   # 核心调度引擎（已重构为模块化目录）
            "database",                   # 数据持久化层（SQLite/Redis）
            # version.json 由更新流程单独写入，避免被仓库旧文件覆盖
            "main_config.template.toml",  # 配置文件模板
            "main.py"                     # 主程序入口
        ]

        # 阶段5: 备份文件 (62.5%)
        await update_progress_manager.update_progress(62, "备份文件", "正在备份现有文件...")
        for item in update_items:
            src_path = os.path.join(root_dir, item)
            if os.path.exists(src_path):
                backup_path = os.path.join(backup_dir, item)
                if os.path.isdir(src_path):
                    shutil.copytree(src_path, backup_path)
                else:
                    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                    shutil.copy2(src_path, backup_path)
                logger.info(f"已备份: {item}")
        await asyncio.sleep(0.5)

        # 阶段6: 更新文件 (75%)
        await update_progress_manager.update_progress(75, "更新文件", "正在安装新版本文件...")

        merge_items = {"plugins", "adapter"}
        # 插件/适配器配置文件：更新时保留本地版本，不覆盖（若本地不存在则会复制生成）。
        excluded_config_names = {"config.toml", "config.json", "config.yaml", "config.yml"}

        for item in update_items:
            new_src_path = os.path.join(extracted_dir, item)
            if os.path.exists(new_src_path):
                dst_path = os.path.join(root_dir, item)

                # plugins/ 与 adapter/ 采用合并更新：不删除本地多余内容，同时保留本地配置文件
                if item in merge_items and os.path.isdir(new_src_path):
                    _merge_copy_tree(
                        new_src_path,
                        dst_path,
                        excluded_names=excluded_config_names,
                    )
                else:
                    # 其它目录/文件维持“替换更新”
                    if os.path.isdir(dst_path):
                        shutil.rmtree(dst_path)
                    elif os.path.exists(dst_path):
                        os.remove(dst_path)

                    if os.path.isdir(new_src_path):
                        shutil.copytree(new_src_path, dst_path)
                    else:
                        shutil.copy2(new_src_path, dst_path)
                logger.info(f"已更新: {item}")
        await asyncio.sleep(0.5)

        # 校验更新完整性（关键文件与 adapter 非空）
        missing = _validate_update(root_dir)
        if missing:
            if update_progress_manager:
                await update_progress_manager.update_progress(
                    82,
                    "校验失败",
                    f"缺少关键文件：{', '.join(missing)}，准备回滚...",
                )
            _restore_from_backup(backup_dir, root_dir, update_items)
            raise Exception(f"更新校验失败，已回滚。缺失: {', '.join(missing)}")

        # 阶段7: 设置文件权限 (80%)
        await update_progress_manager.update_progress(80, "设置权限", "正在设置文件执行权限...")

        # 需要设置执行权限的文件模式
        executable_patterns = [
            "entrypoint.sh",           # Docker 启动脚本
            "*.sh",                    # 所有 shell 脚本
            "WechatAPI/Client/*/XYWechatPad",  # 微信协议二进制文件
        ]

        # 设置 entrypoint.sh 权限（最关键）
        entrypoint_path = os.path.join(root_dir, "entrypoint.sh")
        if os.path.exists(entrypoint_path):
            os.chmod(entrypoint_path, 0o755)
            logger.info(f"已设置执行权限: entrypoint.sh")

        # 设置所有 .sh 文件权限
        for root, dirs, files in os.walk(root_dir):
            for file in files:
                if file.endswith('.sh'):
                    file_path = os.path.join(root, file)
                    try:
                        os.chmod(file_path, 0o755)
                        logger.info(f"已设置执行权限: {os.path.relpath(file_path, root_dir)}")
                    except Exception as e:
                        logger.warning(f"设置权限失败 {file_path}: {e}")

        # 设置 XYWechatPad 二进制文件权限
        wechat_api_dir = os.path.join(root_dir, "WechatAPI", "Client")
        if os.path.exists(wechat_api_dir):
            for protocol_dir in os.listdir(wechat_api_dir):
                protocol_path = os.path.join(wechat_api_dir, protocol_dir)
                if os.path.isdir(protocol_path):
                    xywechat_path = os.path.join(protocol_path, "XYWechatPad")
                    if os.path.exists(xywechat_path):
                        try:
                            os.chmod(xywechat_path, 0o755)
                            logger.info(f"已设置执行权限: WechatAPI/Client/{protocol_dir}/XYWechatPad")
                        except Exception as e:
                            logger.warning(f"设置权限失败 {xywechat_path}: {e}")

        await asyncio.sleep(0.5)

        # 阶段8: 更新版本信息 (90%)
        await update_progress_manager.update_progress(90, "更新版本信息", "正在更新版本配置...")
        latest_version = str(version_info.get("latest_version") or "").strip()
        if not latest_version:
            latest_version = str(version_info.get("version") or "").strip() or "v1.0.0"
        version_info["version"] = latest_version
        version_info["latest_version"] = latest_version
        version_info["update_available"] = False
        version_info["force_update"] = False
        version_info["last_check"] = datetime.now().isoformat()

        version_file = os.path.join(root_dir, "version.json")
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(version_info, f, ensure_ascii=False, indent=2)
        logger.info(f"更新后版本号已写入: {latest_version}")
        await asyncio.sleep(0.5)

        # 阶段9: 清理临时文件 (95%)
        await update_progress_manager.update_progress(95, "清理临时文件", "正在清理临时文件...")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            logger.info(f"已清理临时目录: {temp_dir}")
        # 自动更新只保留最新一份备份
        _cleanup_old_update_backups(root_dir, keep=1, keep_path=backup_dir)
        await asyncio.sleep(0.5)

        # 完成更新
        await update_progress_manager.update_progress(100, "完成", "更新成功！系统即将重启...")
        await update_progress_manager.finish_update(success=True)

        logger.info("更新完成，准备重启系统")

    except Exception as e:
        logger.error(f"更新失败: {str(e)}")
        await update_progress_manager.finish_update(success=False, error=str(e))
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        raise
