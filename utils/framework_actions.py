"""
@input: requests、zipfile/tempfile/shutil/io/os from stdlib；admin.restart_api.restart_system；utils.github_proxy.get_github_url
@output: restart_framework()、update_framework()，提供统一的框架更新与重启入口（自动更新仅保留 1 份 backup_；更新后正确落盘版本号）
@position: 框架运维动作封装层，供管理后台版本更新与 ManagePlugin 复用同一套更新逻辑
@auto-doc: 修改本文件时需同步更新 utils/INDEX.md 与相关调用文档
"""

import asyncio
import io
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from loguru import logger

from utils.github_proxy import get_github_url


_update_lock = asyncio.Lock()


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _version_file() -> Path:
    return _project_root() / "version.json"


def _read_version_info() -> Dict:
    path = _version_file()
    if path.exists():
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 version.json 失败，将使用默认值: {e}")

    return {
        "version": "1.0.0",
        "update_available": False,
        "latest_version": "",
        "update_url": "",
        "update_description": "",
        "last_check": datetime.now().isoformat(),
    }


def _write_version_info(version_info: Dict) -> None:
    try:
        import json

        _version_file().write_text(
            json.dumps(version_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"写入 version.json 失败: {e}")


def _normalize_version(version: str) -> str:
    return str(version or "").strip().lstrip("vV").strip()


def _versions_equal(left: str, right: str) -> bool:
    a = _normalize_version(left)
    b = _normalize_version(right)
    return bool(a) and a == b


def _prefer_version(*candidates: str) -> str:
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text
    return "v1.0.0"


def _plugin_market_base_url() -> str:
    return os.environ.get("PLUGIN_MARKET_BASE_URL", "http://v.sxkiss.top")


def _merge_copy_tree(
    src_dir: Path,
    dst_dir: Path,
    *,
    excluded_names: set[str],
) -> None:
    """合并更新目录，覆盖代码文件但保留现有配置文件与额外文件。"""
    if not src_dir.is_dir():
        return

    if dst_dir.exists() and not dst_dir.is_dir():
        dst_dir.unlink()
    dst_dir.mkdir(parents=True, exist_ok=True)

    for root, _, files in os.walk(src_dir):
        root_path = Path(root)
        rel_root = root_path.relative_to(src_dir)
        dst_root = dst_dir / rel_root if str(rel_root) != "." else dst_dir

        if dst_root.exists() and not dst_root.is_dir():
            dst_root.unlink()
        dst_root.mkdir(parents=True, exist_ok=True)

        for filename in files:
            src_file = root_path / filename
            dst_file = dst_root / filename

            if filename in excluded_names and dst_file.exists():
                continue

            shutil.copy2(src_file, dst_file)


def _validate_update(root_dir: Path) -> List[str]:
    missing: List[str] = []
    required_files = [
        Path("main.py"),
        Path("adapter") / "loader.py",
        Path("adapter") / "base.py",
        Path("bot_core") / "orchestrator.py",
    ]

    for rel_path in required_files:
        if not (root_dir / rel_path).is_file():
            missing.append(rel_path.as_posix())

    adapter_dir = root_dir / "adapter"
    if not adapter_dir.is_dir():
        missing.append("adapter/")
    else:
        entries = [name for name in os.listdir(adapter_dir) if not name.startswith(".")]
        if not entries:
            missing.append("adapter/empty")

    return missing


def _restore_from_backup(backup_dir: Path, root_dir: Path, update_items: List[str]) -> None:
    for item in update_items:
        backup_path = backup_dir / item
        if not backup_path.exists():
            continue

        dst_path = root_dir / item
        if dst_path.is_dir():
            shutil.rmtree(dst_path)
        elif dst_path.exists():
            dst_path.unlink()

        if backup_path.is_dir():
            shutil.copytree(backup_path, dst_path)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, dst_path)


def _list_update_backup_dirs(root_dir: Path) -> List[Path]:
    backups: List[Path] = []
    try:
        for item in root_dir.iterdir():
            name = item.name
            if not item.is_dir():
                continue
            if not name.startswith("backup_"):
                continue
            stamp = name[len("backup_") :]
            if len(stamp) == 14 and stamp.isdigit():
                backups.append(item)
    except Exception as e:
        logger.warning(f"枚举更新备份目录失败: {e}")
    backups.sort(key=lambda p: p.name)
    return backups


def _cleanup_old_update_backups(
    root_dir: Path,
    *,
    keep: int = 1,
    keep_path: Optional[Path] = None,
) -> None:
    """只保留最近 keep 份自动更新备份，避免 backup_ 目录无限堆积。"""
    keep = max(1, int(keep or 1))
    backups = _list_update_backup_dirs(root_dir)
    if not backups:
        return

    keep_set = set()
    if keep_path is not None:
        try:
            keep_set.add(keep_path.resolve())
        except Exception:
            keep_set.add(keep_path)

    # 默认保留最新 keep 份；若指定 keep_path 不在其中，再额外保留它
    latest = backups[-keep:]
    for item in latest:
        try:
            keep_set.add(item.resolve())
        except Exception:
            keep_set.add(item)

    removed = 0
    for item in backups:
        try:
            resolved = item.resolve()
        except Exception:
            resolved = item
        if resolved in keep_set:
            continue
        try:
            shutil.rmtree(item)
            removed += 1
            logger.info(f"已清理旧更新备份: {item.name}")
        except Exception as e:
            logger.warning(f"清理旧更新备份失败 {item}: {e}")

    if removed:
        logger.info(f"自动更新备份清理完成，删除 {removed} 份，保留最新 {keep} 份")


async def _emit_progress(
    progress_manager: Optional[Any],
    progress: int,
    stage: str,
    message: str,
    *,
    error: Optional[str] = None,
) -> None:
    if progress_manager:
        await progress_manager.update_progress(progress, stage, message, error=error)


def _set_executable_permissions(root_dir: Path) -> None:
    entrypoint_path = root_dir / "entrypoint.sh"
    if entrypoint_path.exists():
        os.chmod(entrypoint_path, 0o755)
        logger.info("已设置执行权限: entrypoint.sh")

    for root, _, files in os.walk(root_dir):
        for file in files:
            if file.endswith(".sh"):
                file_path = Path(root) / file
                try:
                    os.chmod(file_path, 0o755)
                    logger.info(f"已设置执行权限: {file_path.relative_to(root_dir).as_posix()}")
                except Exception as e:
                    logger.warning(f"设置权限失败 {file_path}: {e}")

    wechat_api_dir = root_dir / "WechatAPI" / "Client"
    if wechat_api_dir.exists():
        for protocol_dir in wechat_api_dir.iterdir():
            if not protocol_dir.is_dir():
                continue
            xywechat_path = protocol_dir / "XYWechatPad"
            if xywechat_path.exists():
                try:
                    os.chmod(xywechat_path, 0o755)
                    logger.info(
                        f"已设置执行权限: WechatAPI/Client/{protocol_dir.name}/XYWechatPad"
                    )
                except Exception as e:
                    logger.warning(f"设置权限失败 {xywechat_path}: {e}")


def _check_update_via_admin_logic(current_version: str) -> Dict:
    """复用后台界面版本检查逻辑（上游市场 /version/check + 更新本地 version.json）。"""
    base_url = _plugin_market_base_url().rstrip("/")
    url = f"{base_url}/version/check"
    logger.info(f"正在请求版本检查: {url}")

    try:
        response = requests.post(url, json={"current_version": current_version}, timeout=5)
        if response.status_code != 200:
            return {"success": False, "error": f"服务器返回错误状态码: {response.status_code}"}
        result = response.json()
    except Exception as e:
        logger.error(f"连接版本检查服务器失败: {e}")
        result = {"success": False, "error": f"连接版本检查服务器失败: {e}"}

    version_info = _read_version_info()
    latest_version = str(result.get("latest_version", "") or "").strip()
    force_update = bool(result.get("force_update") or result.get("forceUpdate"))

    # force_update 以市场侧为准，不在本地强行覆盖
    version_info["last_check"] = datetime.now().isoformat()
    version_info["force_update"] = force_update
    if force_update:
        version_info["update_available"] = True
        version_info["latest_version"] = latest_version or current_version
        version_info["update_url"] = result.get("update_url", "")
        version_info["update_description"] = result.get("update_description", "")
    elif latest_version and not _versions_equal(latest_version, current_version):
        version_info["update_available"] = True
        version_info["latest_version"] = latest_version
        version_info["update_url"] = result.get("update_url", "")
        version_info["update_description"] = result.get("update_description", "")
    else:
        version_info["update_available"] = False
        if latest_version:
            version_info["latest_version"] = latest_version

    _write_version_info(version_info)

    merged = {"success": True, **version_info}
    merged.update({k: v for k, v in result.items() if k not in merged})
    # 保持本地已同步的 force/update 状态
    merged["force_update"] = version_info["force_update"]
    merged["update_available"] = version_info["update_available"]
    if version_info.get("latest_version"):
        merged["latest_version"] = version_info["latest_version"]
    return merged


async def restart_framework() -> bool:
    """重启框架（容器/进程）。"""
    try:
        from admin.restart_api import restart_system

        await restart_system()
        return True
    except Exception as e:
        logger.error(f"重启框架失败: {e}")

    try:
        os._exit(1)
    except Exception as e:
        logger.error(f"退出进程失败: {e}")
        return False


async def update_framework(
    *,
    progress_manager: Optional[Any] = None,
    auto_restart: bool = True,
) -> Dict[str, str]:
    """更新框架代码（从 GitHub ZIP 下载）。

    说明：
    - 为避免覆盖用户配置，默认不更新 `main_config.toml`。
    - `adapter/` 与 `plugins/` 采用合并更新，保留现有 `config.toml/json/yaml/yml`。
    - 更新完成会在项目根目录生成 `backup_YYYYmmddHHMMSS/` 备份目录，并自动清理旧备份，仅保留 1 份。
    - `version.json` 不从更新包覆盖，更新成功后单独写入最新版本号。
    """
    async with _update_lock:
        root_dir = _project_root()
        temp_dir = Path(tempfile.mkdtemp(prefix="allbot_update_"))

        version_info = _read_version_info()
        current_version = str(version_info.get("version", "") or "").strip() or "1.0.0"

        check_result = _check_update_via_admin_logic(current_version)
        if not (check_result.get("update_available", False) or check_result.get("force_update", False)):
            return {"success": "false", "message": "没有可用的更新"}

        update_items: List[str] = [
            "admin",
            "WechatAPI",
            "utils",
            "adapter",
            "plugins",
            "bot_core",
            "database",
            # version.json 由更新流程单独写入，避免被仓库旧文件覆盖后版本号不变
            "main_config.template.toml",
            "main.py",
            "requirements.txt",
            "pyproject.toml",
            "Dockerfile",
            "docker-compose.yml",
            "entrypoint.sh",
            "redis.conf",
        ]
        merge_items = {"adapter", "plugins"}
        excluded_config_names = {"config.toml", "config.json", "config.yaml", "config.yml"}

        try:
            if progress_manager:
                await progress_manager.start_update()
            await _emit_progress(progress_manager, 0, "初始化", "准备开始更新...")

            zip_url = get_github_url("https://github.com/sxkiss/allbot/archive/refs/heads/main.zip")
            await _emit_progress(progress_manager, 20, "下载更新", "正在从 GitHub 下载最新版本...")
            logger.info(f"开始下载更新: {zip_url}")
            resp = requests.get(zip_url, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(f"下载更新失败: HTTP {resp.status_code}")

            await _emit_progress(progress_manager, 35, "解压文件", "正在解压更新包...")
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(temp_dir)

            extracted_dir = next((p for p in temp_dir.iterdir() if p.is_dir()), None)
            if not extracted_dir:
                raise RuntimeError("解压后未找到有效目录")

            await _emit_progress(progress_manager, 50, "创建备份", "正在备份当前版本...")
            backup_dir = root_dir / ("backup_" + datetime.now().strftime("%Y%m%d%H%M%S"))
            backup_dir.mkdir(parents=True, exist_ok=True)

            await _emit_progress(progress_manager, 60, "备份文件", "正在备份现有文件...")
            for item in update_items:
                src_path = root_dir / item
                if src_path.exists():
                    backup_path = backup_dir / item
                    if src_path.is_dir():
                        shutil.copytree(src_path, backup_path)
                    else:
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_path, backup_path)

                new_src_path = extracted_dir / item
                if not new_src_path.exists():
                    logger.warning(f"更新包中未找到: {item}")
                    continue

                dst_path = root_dir / item
                if item in merge_items and new_src_path.is_dir():
                    _merge_copy_tree(
                        new_src_path,
                        dst_path,
                        excluded_names=excluded_config_names,
                    )
                else:
                    if dst_path.exists():
                        if dst_path.is_dir():
                            shutil.rmtree(dst_path)
                        else:
                            dst_path.unlink()

                    if new_src_path.is_dir():
                        shutil.copytree(new_src_path, dst_path)
                    else:
                        dst_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(new_src_path, dst_path)

                logger.info(f"已更新: {item}")

            missing = _validate_update(root_dir)
            if missing:
                await _emit_progress(
                    progress_manager,
                    82,
                    "校验失败",
                    f"缺少关键文件：{', '.join(missing)}，准备回滚...",
                )
                _restore_from_backup(backup_dir, root_dir, update_items)
                raise RuntimeError(f"更新校验失败，已回滚。缺失: {', '.join(missing)}")

            await _emit_progress(progress_manager, 85, "设置权限", "正在设置文件执行权限...")
            _set_executable_permissions(root_dir)

            # 更新版本信息：以市场 latest 为准落盘，并清理“有更新”标记
            latest_version = str(check_result.get("latest_version", "") or "").strip()
            new_version_info = _read_version_info()
            resolved_version = _prefer_version(
                latest_version,
                new_version_info.get("latest_version"),
                current_version,
                new_version_info.get("version"),
            )
            new_version_info["version"] = resolved_version
            new_version_info["latest_version"] = resolved_version
            new_version_info["update_available"] = False
            new_version_info["force_update"] = False
            new_version_info["last_check"] = datetime.now().isoformat()
            if check_result.get("update_url"):
                new_version_info["update_url"] = check_result.get("update_url")
            if check_result.get("update_description"):
                new_version_info["update_description"] = check_result.get("update_description")
            _write_version_info(new_version_info)
            logger.info(f"更新后版本号已写入: {resolved_version}")

            await _emit_progress(progress_manager, 95, "清理临时文件", "正在清理临时文件...")
            # 自动更新只保留最新一份备份，避免磁盘被 backup_ 堆积
            _cleanup_old_update_backups(root_dir, keep=1, keep_path=backup_dir)
            logger.success("更新完成")

            if progress_manager:
                await progress_manager.finish_update(success=True)

            if auto_restart:
                logger.success("更新完成，准备重启框架")
                await restart_framework()
                return {"success": "true", "message": "更新完成，正在重启框架..."}

            return {"success": "true", "message": "更新完成"}
        except Exception as e:
            logger.error(f"更新失败: {e}")
            if progress_manager:
                await progress_manager.finish_update(success=False, error=str(e))
            return {"success": "false", "message": f"更新失败: {e}"}
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
