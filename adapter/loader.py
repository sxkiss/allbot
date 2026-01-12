import importlib
import threading
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class AdapterInfo:
    """记录适配器的基本信息与运行线程"""

    def __init__(
        self,
        name: str,
        module_name: str,
        class_name: str,
        config_path: Path,
        raw_config: Dict,
    ):
        self.name = name
        self.module_name = module_name
        self.class_name = class_name
        self.config_path = config_path
        self.raw_config = raw_config
        self.thread: Optional[threading.Thread] = None


def start_adapters(base_dir: Optional[Path] = None) -> List[AdapterInfo]:
    """扫描 adapter 目录并启动启用状态的适配器"""
    script_dir = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    adapter_root = script_dir / "adapter"
    if not adapter_root.exists():
        logger.info("未找到 adapter 目录，跳过适配器加载")
        return []

    adapters: List[AdapterInfo] = []
    for entry in sorted(adapter_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("__"):
            logger.debug(f"跳过内部目录 {entry.name}")
            continue

        config_file = entry / "config.toml"
        if not config_file.exists():
            logger.warning(f"适配器 {entry.name} 缺少 config.toml，跳过")
            continue

        try:
            with open(config_file, "rb") as f:
                raw_config = tomllib.load(f)
        except Exception as exc:
            logger.error(f"解析 {config_file} 失败: {exc}")
            continue

        if not _is_enabled(raw_config):
            logger.info(f"适配器 {entry.name} 的配置未启用，跳过")
            continue

        adapter_cfg = raw_config.get("adapter", {})
        module_name = adapter_cfg.get("module") or f"adapter.{entry.name}"
        class_name = adapter_cfg.get("class") or "Adapter"

        info = AdapterInfo(entry.name, module_name, class_name, config_file, raw_config)
        thread = threading.Thread(
            target=_run_adapter,
            name=f"Adapter-{entry.name}",
            args=(info,),
            daemon=True,
        )
        thread.start()
        info.thread = thread
        adapters.append(info)
        logger.success(f"适配器 {entry.name} 已启动线程 {thread.name}")

    if not adapters:
        logger.info("未找到需要启动的适配器")
    return adapters


def _is_enabled(config: Dict) -> bool:
    adapter_section = config.get("adapter")
    if isinstance(adapter_section, dict) and "enabled" in adapter_section:
        return bool(adapter_section.get("enabled"))

    for section in config.values():
        if isinstance(section, dict) and "enable" in section:
            return bool(section.get("enable"))
    return False


def _run_adapter(info: AdapterInfo) -> None:
    try:
        module = importlib.import_module(info.module_name)
    except Exception as exc:
        logger.error(f"适配器 {info.name} 导入模块 {info.module_name} 失败: {exc}")
        return

    adapter_cls = getattr(module, info.class_name, None)
    if adapter_cls is None:
        logger.error(
            f"适配器 {info.name} 未在模块 {info.module_name} 中找到 {info.class_name}"
        )
        return

    try:
        adapter_instance = adapter_cls(info.raw_config, info.config_path)
    except Exception as exc:
        logger.error(f"适配器 {info.name} 初始化失败: {exc}")
        return

    run_method = getattr(adapter_instance, "run", None)
    if callable(run_method):
        try:
            run_method()
        except Exception as exc:
            logger.error(f"适配器 {info.name} 运行异常: {exc}")
    else:
        logger.warning(f"适配器 {info.name} 未实现 run() 方法，线程将结束")
