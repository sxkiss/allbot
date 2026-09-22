"""
@input: GitHub 仓库 URL、插件名、ZIP 内容、可选 requirements.txt
@output: 受限插件安装/卸载结果，含仓库校验、ZIP 安全检查与可选依赖安装
@position: 管理后台插件供应链入口，负责将远程插件安装收敛到受控流程
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import os
import tempfile
import shutil
import zipfile
import io
import subprocess
import sys
import stat
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse
from loguru import logger
import requests

# 导入 GitHub 反代工具
from utils.github_proxy import get_github_url


class PluginInstaller:
    """插件安装服务类"""

    # 常量定义
    DOWNLOAD_TIMEOUT = 30
    SAFE_NAME_RE = r"^[A-Za-z0-9_.-]+$"

    def __init__(self, plugins_dir: str = "plugins"):
        """
        初始化插件安装器

        Args:
            plugins_dir: 插件根目录路径
        """
        self.plugins_dir = plugins_dir

    def _validate_plugin_name(self, plugin_name: str) -> str:
        import re

        safe_name = str(plugin_name or "").strip()
        if not safe_name or not re.fullmatch(self.SAFE_NAME_RE, safe_name):
            raise ValueError("插件名称只允许字母、数字、点、下划线和中划线")
        if safe_name in {".", ".."}:
            raise ValueError("非法插件名称")
        return safe_name

    def _normalize_github_url(self, github_url: str) -> str:
        """
        标准化 GitHub URL

        Args:
            github_url: 原始 GitHub URL

        Returns:
            标准化后的 URL（owner/repo）
        """
        parsed = urlparse(str(github_url or "").strip())
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            raise ValueError("仅支持 https://github.com/<owner>/<repo> 格式的仓库地址")

        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2:
            raise ValueError("GitHub 仓库地址必须是 https://github.com/<owner>/<repo>")
        return "/".join(parts)

    def _validate_zip_entry(self, target_root: Path, member: zipfile.ZipInfo) -> None:
        member_name = member.filename.replace("\\", "/")
        if member_name.startswith("/") or ".." in Path(member_name).parts:
            raise ValueError(f"ZIP 包含不安全路径: {member.filename}")

        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"ZIP 包含符号链接，已拒绝: {member.filename}")

        destination = (target_root / member_name).resolve(strict=False)
        destination.relative_to(target_root.resolve(strict=False))

    def _download_from_github(self, github_url: str) -> bytes:
        """
        从 GitHub 下载插件 ZIP 文件（支持反代加速）

        Args:
            github_url: GitHub 仓库 URL（已标准化，格式：owner/repo）

        Returns:
            下载的文件内容（字节）

        Raises:
            Exception: 下载失败时抛出异常
        """
        # 尝试 main 分支
        original_url = f"https://github.com/{github_url}/archive/refs/heads/main.zip"
        zip_url = get_github_url(original_url)  # 使用反代
        logger.info(f"正在从 {zip_url} 下载插件...")

        try:
            response = requests.get(zip_url, timeout=self.DOWNLOAD_TIMEOUT)
            if response.status_code == 200:
                logger.info(f"从 main 分支下载成功，文件大小: {len(response.content)} 字节")
                return response.content
        except Exception as e:
            logger.warning(f"从 main 分支下载失败: {e}")

        # 尝试 master 分支
        original_master_url = f"https://github.com/{github_url}/archive/refs/heads/master.zip"
        master_url = get_github_url(original_master_url)  # 使用反代
        logger.info(f"尝试从 master 分支下载: {master_url}")

        response = requests.get(master_url, timeout=self.DOWNLOAD_TIMEOUT)
        if response.status_code != 200:
            raise Exception(f"下载插件失败: HTTP {response.status_code}")

        logger.info(f"从 master 分支下载成功，文件大小: {len(response.content)} 字节")
        return response.content

    def _extract_zip(self, zip_content: bytes, temp_dir: str) -> str:
        """
        解压 ZIP 文件到临时目录

        Args:
            zip_content: ZIP 文件内容
            temp_dir: 临时目录路径

        Returns:
            解压后的插件源目录路径
        """
        logger.info(f"下载完成，文件大小: {len(zip_content)} 字节")
        logger.info(f"解压 ZIP 文件到: {temp_dir}")

        z = zipfile.ZipFile(io.BytesIO(zip_content))
        target_root = Path(temp_dir)
        for member in z.infolist():
            self._validate_zip_entry(target_root, member)
        z.extractall(temp_dir)

        # ZIP 文件解压后通常会有一个包含所有文件的顶级目录
        extracted_dirs = os.listdir(temp_dir)
        if len(extracted_dirs) == 1:
            return os.path.join(temp_dir, extracted_dirs[0])
        else:
            return temp_dir

    def _backup_config(self, plugin_dir: str) -> Optional[bytes]:
        """
        备份插件配置文件

        Args:
            plugin_dir: 插件目录路径

        Returns:
            配置文件内容（如果存在），否则返回 None
        """
        config_path = os.path.join(plugin_dir, "config.toml")
        if os.path.exists(config_path):
            with open(config_path, "rb") as f:
                return f.read()
        return None

    def _restore_config(self, plugin_dir: str, config_backup: Optional[bytes]):
        """
        恢复插件配置文件

        Args:
            plugin_dir: 插件目录路径
            config_backup: 备份的配置文件内容
        """
        if config_backup:
            config_path = os.path.join(plugin_dir, "config.toml")
            with open(config_path, "wb") as f:
                f.write(config_backup)
            logger.info(f"已恢复配置文件: {config_path}")

    def _copy_plugin_files(self, source_dir: str, target_dir: str):
        """
        复制插件文件到目标目录

        Args:
            source_dir: 源目录路径
            target_dir: 目标目录路径
        """
        for item in os.listdir(source_dir):
            s = os.path.join(source_dir, item)
            d = os.path.join(target_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    def _install_requirements(self, plugin_dir: str) -> Tuple[bool, str]:
        """
        安装插件依赖

        Args:
            plugin_dir: 插件目录路径

        Returns:
            (是否成功, 错误信息)
        """
        requirements_path = os.path.join(plugin_dir, "requirements.txt")
        if not os.path.exists(requirements_path):
            logger.info("未找到 requirements.txt，跳过依赖安装")
            return True, ""

        logger.info(f"正在安装依赖: {requirements_path}")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", requirements_path],
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode != 0:
                error_msg = f"依赖安装失败: {result.stderr}"
                logger.error(error_msg)
                return False, error_msg

            logger.info("依赖安装成功")
            return True, ""
        except subprocess.TimeoutExpired:
            error_msg = "依赖安装超时（5分钟）"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"依赖安装异常: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    def install_plugin(
        self,
        plugin_name: str,
        github_url: str,
        install_dependencies: bool = True
    ) -> Dict[str, any]:
        """
        安装插件

        Args:
            plugin_name: 插件名称
            github_url: GitHub 仓库 URL
            install_dependencies: 是否安装依赖

        Returns:
            安装结果字典 {"success": bool, "message": str, "error": str}
        """
        temp_dir = None
        plugin_name = self._validate_plugin_name(plugin_name)
        plugin_dir = os.path.join(self.plugins_dir, plugin_name)
        config_backup = None

        try:
            # 1. 标准化 GitHub URL
            github_url = self._normalize_github_url(github_url)

            # 2. 创建临时目录
            temp_dir = tempfile.mkdtemp()

            # 3. 备份现有配置
            if os.path.exists(plugin_dir):
                config_backup = self._backup_config(plugin_dir)
                logger.info(f"插件已存在，将进行更新: {plugin_name}")
                shutil.rmtree(plugin_dir)

            # 4. 下载插件
            zip_content = self._download_from_github(github_url)

            # 5. 解压插件
            source_dir = self._extract_zip(zip_content, temp_dir)

            # 6. 创建目标目录
            os.makedirs(plugin_dir, exist_ok=True)

            # 7. 复制文件
            self._copy_plugin_files(source_dir, plugin_dir)
            logger.info(f"插件文件已复制到: {plugin_dir}")

            # 8. 恢复配置
            self._restore_config(plugin_dir, config_backup)

            # 9. 安装依赖
            if install_dependencies:
                success, error = self._install_requirements(plugin_dir)
                if not success:
                    return {
                        "success": False,
                        "error": error,
                        "message": "插件文件已安装，但依赖安装失败"
                    }

            # 10. 返回成功结果
            return {
                "success": True,
                "message": f"插件 {plugin_name} 安装成功",
                "plugin_dir": plugin_dir
            }

        except Exception as e:
            error_msg = f"安装插件失败: {str(e)}"
            logger.error(error_msg)
            logger.exception(e)

            # 清理失败的安装
            if os.path.exists(plugin_dir):
                try:
                    shutil.rmtree(plugin_dir)
                except Exception as cleanup_error:
                    logger.error(f"清理失败的安装目录时出错: {cleanup_error}")

            return {
                "success": False,
                "error": error_msg
            }

        finally:
            # 清理临时目录
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"清理临时目录失败: {e}")

    def uninstall_plugin(self, plugin_name: str) -> Dict[str, any]:
        """
        卸载插件

        Args:
            plugin_name: 插件名称

        Returns:
            卸载结果字典 {"success": bool, "message": str, "error": str}
        """
        plugin_dir = os.path.join(self.plugins_dir, plugin_name)

        if not os.path.exists(plugin_dir):
            return {
                "success": False,
                "error": f"插件不存在: {plugin_name}"
            }

        try:
            shutil.rmtree(plugin_dir)
            logger.info(f"插件已卸载: {plugin_name}")
            return {
                "success": True,
                "message": f"插件 {plugin_name} 已成功卸载"
            }
        except Exception as e:
            error_msg = f"卸载插件失败: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg
            }
