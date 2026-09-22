#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
适配器管理模块

提供适配器配置的读取、修改和状态管理功能。
使用 tomlkit 保留配置文件的格式和注释。
"""

import fcntl
from pathlib import Path
from typing import Dict, List, Optional

import tomlkit
from loguru import logger


class AdapterManager:
    """适配器管理器"""

    def __init__(self, adapter_root: Optional[Path] = None):
        """
        初始化适配器管理器

        Args:
            adapter_root: 适配器根目录，默认为项目根目录下的 adapter 目录
        """
        if adapter_root is None:
            script_dir = Path(__file__).resolve().parent.parent
            adapter_root = script_dir / "adapter"
        self.adapter_root = Path(adapter_root)

    def list_adapters(self) -> List[Dict]:
        """
        获取所有适配器列表及其状态

        Returns:
            适配器信息列表，每项包含: name, enabled, platform, config_path
        """
        if not self.adapter_root.exists():
            logger.warning(f"适配器目录不存在: {self.adapter_root}")
            return []

        adapters = []
        for entry in sorted(self.adapter_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("__"):
                continue

            config_file = entry / "config.toml"
            if not config_file.exists():
                logger.warning(f"适配器 {entry.name} 缺少 config.toml")
                continue

            try:
                config = self._read_toml(config_file)
                doc_path = entry / "README.md"
                doc_summary = ""
                if doc_path.exists():
                    doc_summary = self._extract_doc_summary(self._read_text(doc_path))
                adapter_info = {
                    "name": entry.name,
                    "enabled": self._get_enabled_status(config),
                    "platform": self._get_platform_name(config, entry.name),
                    "config_path": str(config_file),
                    "description": doc_summary,
                    "doc_available": doc_path.exists(),
                }
                adapters.append(adapter_info)
            except Exception as e:
                logger.error(f"读取适配器 {entry.name} 配置失败: {e}")
                continue

        return adapters

    def get_adapter_config(self, adapter_name: str) -> Optional[Dict]:
        """
        获取指定适配器的配置

        Args:
            adapter_name: 适配器名称

        Returns:
            配置字典，如果不存在返回 None
        """
        config_file = self.adapter_root / adapter_name / "config.toml"
        if not config_file.exists():
            logger.warning(f"适配器配置文件不存在: {config_file}")
            return None

        try:
            return self._read_toml(config_file)
        except Exception as e:
            logger.error(f"读取适配器 {adapter_name} 配置失败: {e}")
            return None

    def get_adapter_doc(self, adapter_name: str) -> Optional[str]:
        """
        获取适配器说明文档内容

        Args:
            adapter_name: 适配器名称

        Returns:
            文档内容，若不存在返回 None
        """
        doc_path = self.adapter_root / adapter_name / "README.md"
        if not doc_path.exists():
            return None

        try:
            return self._read_text(doc_path)
        except Exception as e:
            logger.error(f"读取适配器 {adapter_name} 文档失败: {e}")
            return None

    def update_adapter_status(self, adapter_name: str, enabled: bool) -> bool:
        """
        更新适配器的启用状态

        Args:
            adapter_name: 适配器名称
            enabled: 是否启用

        Returns:
            是否更新成功
        """
        config_file = self.adapter_root / adapter_name / "config.toml"
        if not config_file.exists():
            logger.error(f"适配器配置文件不存在: {config_file}")
            return False

        try:
            # 使用文件锁防止并发修改
            with open(config_file, "r+", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # 读取配置
                    f.seek(0)
                    config = tomlkit.load(f)

                    # 更新状态
                    self._set_enabled_status(config, enabled)

                    # 写回文件
                    f.seek(0)
                    f.truncate()
                    tomlkit.dump(config, f)

                    logger.success(
                        f"适配器 {adapter_name} 状态已更新为: {'启用' if enabled else '禁用'}"
                    )
                    return True
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        except Exception as e:
            logger.error(f"更新适配器 {adapter_name} 状态失败: {e}")
            return False

    def _read_toml(self, path: Path) -> Dict:
        """读取 TOML 配置文件"""
        with open(path, "r", encoding="utf-8") as f:
            return tomlkit.load(f)

    def _read_text(self, path: Path) -> str:
        """读取文本文件"""
        return path.read_text(encoding="utf-8")

    def _extract_doc_summary(self, doc_text: str) -> str:
        """从说明文档中提取摘要"""
        for raw_line in doc_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            return line
        return ""

    def _get_enabled_status(self, config: Dict) -> bool:
        """
        获取适配器的启用状态

        优先检查 [adapter].enabled，其次检查各平台的 enable 字段
        """
        # 检查 [adapter].enabled
        adapter_section = config.get("adapter")
        if isinstance(adapter_section, dict) and "enabled" in adapter_section:
            return bool(adapter_section.get("enabled"))

        # 检查其他 section 的 enable 字段
        for section in config.values():
            if isinstance(section, dict) and "enable" in section:
                return bool(section.get("enable"))

        return False

    def _set_enabled_status(self, config: Dict, enabled: bool):
        """
        设置适配器的启用状态

        同时更新 [adapter].enabled 和平台的 enable 字段
        """
        # 更新 [adapter].enabled
        if "adapter" in config and isinstance(config["adapter"], dict):
            config["adapter"]["enabled"] = enabled

        # 更新其他 section 的 enable 字段
        for key, section in config.items():
            if key != "adapter" and isinstance(section, dict) and "enable" in section:
                section["enable"] = enabled

    def _get_platform_name(self, config: Dict, default_name: str) -> str:
        """获取平台名称"""
        adapter_section = config.get("adapter", {})
        if isinstance(adapter_section, dict):
            return adapter_section.get("name", default_name)
        return default_name


# 全局适配器管理器实例
adapter_manager = AdapterManager()
