"""
@input: adapter/*/config.toml（enabled 开关、module/class 配置）
@output: 启动各平台适配器线程，返回 AdapterInfo 列表
@position: 适配器层入口 — 扫描并启动启用状态的平台适配器，内置 web 预实例去重
@auto-doc: Update header and folder INDEX.md when this file changes

适配器加载器：扫描 adapter 子目录，按 config.toml 启动启用适配器。
"""

import importlib
import threading
import tomllib
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


# 进程内适配器实例注册表：后台与适配器同进程，可据此拿到运行实例
_ADAPTERS_LOCK = threading.Lock()
_RUNNING_ADAPTERS: Dict[str, object] = {}
_ADAPTER_INFOS: Dict[str, "AdapterInfo"] = {}


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
        # 运行期信息：实例由 _run_adapter 实例化后回填
        self.instance: Optional[object] = None
        self.running: bool = False
        self.error: str = ""


def get_running_adapter(name: str) -> Optional[object]:
    """按名称获取正在运行的适配器实例（不存在返回 None）。"""
    with _ADAPTERS_LOCK:
        return _RUNNING_ADAPTERS.get(name)


def list_running_adapters() -> List["AdapterInfo"]:
    """返回当前处于运行状态的适配器信息列表。"""
    with _ADAPTERS_LOCK:
        return [info for info in _ADAPTER_INFOS.values() if info.running]


def get_adapter_info(name: str) -> Optional["AdapterInfo"]:
    """按名称获取适配器信息（含已停止实例）。"""
    with _ADAPTERS_LOCK:
        return _ADAPTER_INFOS.get(name)



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
        if _is_preinitialized(entry.name):
            logger.info(f"适配器 {entry.name} 已存在运行实例，跳过重复启动")
            continue

        adapter_cfg = raw_config.get("adapter", {})
        module_name = adapter_cfg.get("module") or f"adapter.{entry.name}"
        class_name = adapter_cfg.get("class") or "Adapter"

        info = AdapterInfo(entry.name, module_name, class_name, config_file, raw_config)
        with _ADAPTERS_LOCK:
            _ADAPTER_INFOS[info.name] = info
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


def is_adapter_enabled(adapter_name: str, base_dir: Optional[Path] = None) -> bool:
    """只读判断某适配器是否已在 config.toml 中启用（供主框架接管判断）。"""
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    config_file = root / "adapter" / adapter_name / "config.toml"
    if not config_file.exists():
        return False
    try:
        with open(config_file, "rb") as file:
            return _is_enabled(tomllib.load(file))
    except Exception:
        return False


def get_adapter_config(adapter_name: str, base_dir: Optional[Path] = None) -> Dict:
    """只读读取适配器 config.toml（供主框架解析会话路径等）。"""
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    config_file = root / "adapter" / adapter_name / "config.toml"
    try:
        with open(config_file, "rb") as file:
            return tomllib.load(file)
    except Exception:
        return {}


def start_adapter(adapter_name: str, base_dir: Optional[Path] = None) -> bool:
    """运行时启动单个适配器（热插拔，无需重启主服务）。"""
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    entry = root / "adapter" / adapter_name
    config_file = entry / "config.toml"
    if not config_file.exists():
        logger.error(f"适配器 {adapter_name} 不存在（缺少 config.toml）")
        return False
    try:
        with open(config_file, "rb") as file:
            raw_config = tomllib.load(file)
    except Exception as exc:
        logger.error(f"解析适配器 {adapter_name} 配置失败: {exc}")
        return False
    if not _is_enabled(raw_config):
        logger.warning(f"适配器 {adapter_name} 未启用，拒绝启动")
        return False
    with _ADAPTERS_LOCK:
        existing = _ADAPTER_INFOS.get(adapter_name)
        if existing is not None and existing.running:
            logger.info(f"适配器 {adapter_name} 已在运行，跳过")
            return True
    adapter_cfg = raw_config.get("adapter", {})
    module_name = adapter_cfg.get("module") or f"adapter.{adapter_name}"
    class_name = adapter_cfg.get("class") or "Adapter"
    info = AdapterInfo(adapter_name, module_name, class_name, config_file, raw_config)
    with _ADAPTERS_LOCK:
        _ADAPTER_INFOS[adapter_name] = info
    thread = threading.Thread(
        target=_run_adapter,
        name=f"Adapter-{adapter_name}",
        args=(info,),
        daemon=True,
    )
    thread.start()
    info.thread = thread
    logger.success(f"适配器 {adapter_name} 已热启动")
    return True


def stop_adapter(adapter_name: str, timeout: float = 8.0) -> bool:
    """运行时停止单个适配器（热插拔，无需重启主服务）。"""
    with _ADAPTERS_LOCK:
        info = _ADAPTER_INFOS.get(adapter_name)
        instance = _RUNNING_ADAPTERS.get(adapter_name)
    if info is None and instance is None:
        logger.warning(f"适配器 {adapter_name} 不存在或未运行")
        return False
    stop_method = getattr(instance, "stop", None)
    if callable(stop_method):
        try:
            stop_method()
        except Exception as exc:
            logger.warning(f"适配器 {adapter_name} stop() 异常: {exc}")
    if info is not None and info.thread is not None and info.thread.is_alive():
        info.thread.join(timeout=timeout)
        if info.thread.is_alive():
            logger.warning(f"适配器 {adapter_name} 线程未在 {timeout}s 内退出（daemon 线程）")
    with _ADAPTERS_LOCK:
        _RUNNING_ADAPTERS.pop(adapter_name, None)
    if info is not None:
        _set_adapter_state(info, False, "")
    logger.success(f"适配器 {adapter_name} 已热停止")
    return True


def restart_adapter(adapter_name: str, base_dir: Optional[Path] = None) -> bool:
    """运行时重启单个适配器。"""
    stop_adapter(adapter_name)
    return start_adapter(adapter_name, base_dir)


def _send_adapter_error_notification(adapter_name: str, error_msg: str) -> None:
    """发送适配器错误通知（非阻塞，兼容无事件循环线程）"""
    try:
        from utils.notification_service import get_notification_service
        notification_service = get_notification_service()
        if not notification_service:
            return
        notification_service.schedule(
            lambda: notification_service.send_adapter_error_notification(
                adapter_name,
                error_msg,
            )
        )
        logger.info(f"已触发适配器 {adapter_name} 错误通知")
    except Exception as e:
        logger.warning(f"发送适配器 {adapter_name} 错误通知失败: {e}")


def _set_adapter_state(info: AdapterInfo, running: bool, error: Optional[str] = None) -> None:
    """在注册表锁内更新运行状态，保证后台读到的状态一致。"""
    with _ADAPTERS_LOCK:
        info.running = running
        if error is not None:
            info.error = error


def _run_adapter(info: AdapterInfo) -> None:
    try:
        module = importlib.import_module(info.module_name)
    except Exception as exc:
        logger.error(f"适配器 {info.name} 导入模块 {info.module_name} 失败: {exc}")
        _set_adapter_state(info, False, f"导入模块失败: {exc}")
        _send_adapter_error_notification(info.name, f"导入模块失败: {exc}")
        return

    adapter_cls = getattr(module, info.class_name, None)
    if adapter_cls is None:
        logger.error(
            f"适配器 {info.name} 未在模块 {info.module_name} 中找到 {info.class_name}"
        )
        _set_adapter_state(info, False, f"未找到类 {info.class_name}")
        _send_adapter_error_notification(info.name, f"未找到类 {info.class_name}")
        return

    try:
        adapter_instance = adapter_cls(info.raw_config, info.config_path)
    except Exception as exc:
        logger.error(f"适配器 {info.name} 初始化失败: {exc}")
        _set_adapter_state(info, False, f"初始化失败: {exc}")
        _send_adapter_error_notification(info.name, f"初始化失败: {exc}")
        return

    # 实例化成功即登记，后台可随时取到实例查询状态（即使 run() 结束后也保留）
    with _ADAPTERS_LOCK:
        info.instance = adapter_instance
        info.running = True
        info.error = ""
        _RUNNING_ADAPTERS[info.name] = adapter_instance

    run_method = getattr(adapter_instance, "run", None)
    if callable(run_method):
        try:
            run_method()
        except Exception as exc:
            logger.error(f"适配器 {info.name} 运行异常: {exc}")
            _set_adapter_state(info, False, f"运行异常: {exc}")
            _send_adapter_error_notification(info.name, f"运行异常: {exc}")
        finally:
            _set_adapter_state(info, False)
    else:
        logger.warning(f"适配器 {info.name} 未实现 run() 方法，线程将结束")
        _set_adapter_state(info, False)
    # 注意：不从 _RUNNING_ADAPTERS 移除实例，后台仍需查询其状态


def _is_preinitialized(adapter_name: str) -> bool:
    """检测由其他模块提前初始化的适配器，避免重复启动。"""
    if adapter_name != "web":
        return False
    try:
        from adapter.web import get_web_adapter
    except Exception:
        return False
    return get_web_adapter() is not None
