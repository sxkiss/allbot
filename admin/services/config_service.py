"""
@input: tomlkit、main_config.toml / 插件与适配器 config.toml、字段 schema 元数据
@output: 可视化配置 schema、结构化读写与保留注释的 TOML 更新能力
@position: 管理后台配置服务层，把原始 TOML 转成普通用户可编辑的表单数据
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import ast
import fcntl
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import tomlkit
from loguru import logger
from tomlkit.items import AoT, Array, Bool, Float, Integer, Item, Table


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MAIN_CONFIG_PATH = PROJECT_ROOT / "main_config.toml"


def _field(
    key: str,
    label: str,
    field_type: str,
    *,
    description: str = "",
    options: Optional[List[Dict[str, str]]] = None,
    placeholder: str = "",
    secret: bool = False,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    advanced: bool = False,
    readonly: bool = False,
    unit: str = "",
    default: Any = None,
    item_label: str = "项",
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "key": key,
        "label": label,
        "type": field_type,
        "description": description,
        "placeholder": placeholder,
        "secret": secret,
        "advanced": advanced,
        "readonly": readonly,
        "unit": unit,
        "item_label": item_label,
    }
    if options is not None:
        data["options"] = options
    if min_value is not None:
        data["min"] = min_value
    if max_value is not None:
        data["max"] = max_value
    if default is not None:
        data["default"] = default
    return data


def _section(
    key: str,
    title: str,
    description: str,
    fields: Sequence[Dict[str, Any]],
    *,
    icon: str = "bi-gear",
) -> Dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "icon": icon,
        "fields": list(fields),
    }


def _option(value: str, label: str) -> Dict[str, str]:
    return {"value": value, "label": label}


MAIN_CONFIG_SCHEMA: List[Dict[str, Any]] = [
    _section(
        "Protocol",
        "协议设置",
        "选择微信协议版本。普通部署保持默认即可。",
        [
            _field(
                "version",
                "协议版本",
                "select",
                description="不同协议兼容不同客户端环境。",
                options=[
                    _option("869", "869（推荐）"),
                    _option("ipad", "iPad"),
                    _option("pad", "Pad"),
                    _option("mac", "Mac"),
                    _option("ipad2", "iPad2"),
                    _option("car", "Car"),
                    _option("win", "Windows"),
                ],
            )
        ],
        icon="bi-cpu",
    ),
    _section(
        "Framework",
        "框架设置",
        "机器人运行框架类型。",
        [
            _field(
                "type",
                "框架类型",
                "select",
                description="当前仅支持 wechat。",
                options=[_option("wechat", "微信 (wechat)")],
            )
        ],
        icon="bi-box",
    ),
    _section(
        "WechatAPIServer",
        "微信接口服务",
        "连接 WechatAPI / 消息队列相关设置。",
        [
            _field("host", "服务地址", "text", description="WechatAPI 服务主机地址。", placeholder="127.0.0.1"),
            _field("port", "服务端口", "number", description="WechatAPI 服务端口。", min_value=1, max_value=65535),
            _field(
                "mode",
                "运行模式",
                "select",
                description="生产环境建议使用 release。",
                options=[_option("debug", "调试 (debug)"), _option("release", "生产 (release)")],
            ),
            _field("admin-key", "管理密钥", "password", description="869 管理密钥，部署时填写。", secret=True),
            _field(
                "login-qrcode-proxy",
                "登录二维码代理",
                "text",
                description="可选，例如 socks5://user:pass@ip:port。",
                placeholder="socks5://127.0.0.1:1080",
                advanced=True,
            ),
            _field("redis-host", "Redis 地址", "text", placeholder="127.0.0.1"),
            _field("redis-port", "Redis 端口", "number", min_value=1, max_value=65535),
            _field("redis-password", "Redis 密码", "password", secret=True, description="没有密码可留空。"),
            _field("redis-db", "Redis 数据库编号", "number", min_value=0, max_value=15),
            _field(
                "message-consumer-workers",
                "消息消费并发数",
                "number",
                description="大于 1 可避免慢插件阻塞全站。",
                min_value=1,
                max_value=64,
            ),
            _field("enable-websocket", "启用 WebSocket 收消息", "boolean"),
            _field(
                "ws-url",
                "WebSocket 地址",
                "text",
                description="869 建议使用 /ws/GetSyncMsg，程序会自动追加 key。",
                placeholder="ws://127.0.0.1:9000/ws/GetSyncMsg",
            ),
            _field("enable-rabbitmq", "启用 RabbitMQ 收消息", "boolean"),
            _field("rabbitmq-host", "RabbitMQ 地址", "text", placeholder="127.0.0.1", advanced=True),
            _field("rabbitmq-port", "RabbitMQ 端口", "number", min_value=1, max_value=65535, advanced=True),
            _field("rabbitmq-user", "RabbitMQ 用户名", "text", advanced=True),
            _field("rabbitmq-password", "RabbitMQ 密码", "password", secret=True, advanced=True),
            _field("rabbitmq-queue", "RabbitMQ 队列名", "text", advanced=True),
        ],
        icon="bi-hdd-network",
    ),
    _section(
        "Admin",
        "管理后台",
        "后台登录与访问安全设置。",
        [
            _field("enabled", "启用管理后台", "boolean"),
            _field("host", "监听地址", "text", description="0.0.0.0 表示允许外部访问。", placeholder="0.0.0.0"),
            _field("port", "监听端口", "number", min_value=1, max_value=65535),
            _field("username", "登录用户名", "text"),
            _field("password", "登录密码", "password", secret=True, description="请使用强密码，不要使用示例值。"),
            _field(
                "secret-key",
                "会话签名密钥",
                "password",
                secret=True,
                advanced=True,
                description="可留空；启动时会自动生成并写回。",
            ),
            _field(
                "session-cookie-secure",
                "仅 HTTPS 发送 Cookie",
                "boolean",
                description="正式 HTTPS 部署请开启。",
                advanced=True,
            ),
            _field(
                "cors-origins",
                "允许跨域来源",
                "list",
                description="生产环境请只保留实际域名。",
                item_label="来源",
                advanced=True,
            ),
            _field("debug", "调试模式", "boolean"),
            _field(
                "log_level",
                "日志级别",
                "select",
                options=[
                    _option("DEBUG", "DEBUG"),
                    _option("INFO", "INFO"),
                    _option("WARNING", "WARNING"),
                    _option("ERROR", "ERROR"),
                    _option("CRITICAL", "CRITICAL"),
                ],
            ),
        ],
        icon="bi-shield-lock",
    ),
    _section(
        "Logging",
        "日志设置",
        "控制日志输出方式与保留策略。",
        [
            _field("enable_file_log", "启用文件日志", "boolean"),
            _field("enable_console_log", "启用控制台日志", "boolean"),
            _field("enable_json_format", "JSON 格式日志", "boolean", advanced=True),
            _field("max_log_files", "最大日志文件数", "number", min_value=1, max_value=1000),
            _field(
                "log_rotation",
                "日志轮转周期",
                "text",
                description="例如 1 day / 1 week / 100 MB。",
                placeholder="1 day",
            ),
        ],
        icon="bi-journal-text",
    ),
    _section(
        "Performance",
        "性能监控",
        "系统资源监控与告警阈值。",
        [
            _field("enabled", "启用性能监控", "boolean"),
            _field("monitoring_interval", "监控间隔", "number", unit="秒", min_value=5, max_value=3600),
            _field("max_history_size", "最大历史记录数", "number", min_value=10, max_value=100000, advanced=True),
            _field("cpu_alert_threshold", "CPU 告警阈值", "number", unit="%", min_value=1, max_value=100),
            _field("memory_alert_threshold", "内存告警阈值", "number", unit="%", min_value=1, max_value=100),
            _field("memory_low_threshold_mb", "可用内存告警阈值", "number", unit="MB", min_value=1),
        ],
        icon="bi-speedometer2",
    ),
    _section(
        "XYBot",
        "机器人核心",
        "机器人身份、管理员、数据库与常用运行开关。",
        [
            _field("version", "版本号", "text", readonly=True, description="框架版本，请勿修改。"),
            _field("enable-wechat-login", "启用微信登录", "boolean", description="仅适配器输入时可关闭。"),
            _field("ignore-protection", "忽略风控保护", "boolean", description="建议保持关闭。"),
            _field("enable-group-wakeup", "启用群聊唤醒词", "boolean"),
            _field("group-wakeup-words", "群聊唤醒词", "list", item_label="唤醒词"),
            _field("robot-names", "机器人名称", "list", description="用于识别 @机器人。", item_label="名称"),
            _field("robot-wxids", "机器人 wxid", "list", description="用于识别 at_list。", item_label="wxid"),
            _field(
                "github-proxy",
                "GitHub 加速地址",
                "text",
                description="留空表示直连；填写时请以 / 结尾。",
                placeholder="https://ghfast.top/",
            ),
            _field("XYBotDB-url", "主数据库地址", "text", advanced=True),
            _field("msgDB-url", "消息数据库地址", "text", advanced=True),
            _field("keyvalDB-url", "键值数据库地址", "text", advanced=True),
            _field("admins", "管理员 wxid", "list", description="可从消息日志中获取。", item_label="wxid"),
            _field("disabled-plugins", "禁用插件列表", "list", item_label="插件", advanced=True),
            _field("timezone", "时区", "text", placeholder="Asia/Shanghai"),
            _field("auto-restart", "配置变更自动重启", "boolean", description="仅建议开发环境开启。"),
            _field(
                "files-cleanup-days",
                "图片自动清理天数",
                "number",
                description="0 表示禁用自动清理。",
                min_value=0,
                max_value=3650,
                unit="天",
            ),
        ],
        icon="bi-robot",
    ),
    _section(
        "MessageFilter",
        "消息过滤",
        "控制机器人处理哪些用户/群的消息。",
        [
            _field(
                "ignore-mode",
                "过滤模式",
                "select",
                description="无限制 / 白名单 / 黑名单。",
                options=[
                    _option("None", "处理全部消息"),
                    _option("Whitelist", "仅处理白名单"),
                    _option("Blacklist", "屏蔽黑名单"),
                ],
            ),
            _field("whitelist", "白名单", "list", description="个人 wxid 或群 ID。", item_label="ID"),
            _field("blacklist", "黑名单", "list", description="个人 wxid 或群 ID。", item_label="ID"),
        ],
        icon="bi-funnel",
    ),
    _section(
        "AutoRestart",
        "自动重启监控",
        "掉线检测与自动拉起策略。",
        [
            _field("enabled", "启用自动重启监控", "boolean"),
            _field("check-interval", "检查间隔", "number", unit="秒", min_value=5),
            _field("offline-threshold", "离线阈值", "number", unit="秒", min_value=30),
            _field("max-restart-attempts", "最大重启次数", "number", min_value=1, max_value=50),
            _field("restart-cooldown", "重启冷却时间", "number", unit="秒", min_value=0),
            _field("check-offline-trace", "检查掉线追踪日志", "boolean", advanced=True),
            _field("failure-count-threshold", "连续失败阈值", "number", min_value=1, advanced=True),
            _field("reset-threshold-multiplier", "重置阈值倍数", "number", min_value=1, advanced=True),
        ],
        icon="bi-arrow-repeat",
    ),
    _section(
        "Notification",
        "系统通知",
        "离线/重连/重启等事件推送。更完整模板编辑也可在“通知设置”页完成。",
        [
            _field("enabled", "启用通知", "boolean"),
            _field("token", "通知 Token / API Key", "password", secret=True),
            _field(
                "channel",
                "通知渠道",
                "select",
                options=[
                    _option("wechat", "微信公众号"),
                    _option("sms", "短信"),
                    _option("mail", "邮件"),
                    _option("webhook", "Webhook"),
                    _option("cp", "企业微信"),
                ],
            ),
            _field("template", "模板类型", "text", advanced=True, placeholder="html"),
            _field("topic", "群组编码", "text", description="不填仅发送给自己。"),
            _field("heartbeatThreshold", "心跳失败阈值", "number", min_value=1),
            _field("triggers.offline", "离线通知", "boolean"),
            _field("triggers.reconnect", "重连通知", "boolean"),
            _field("triggers.restart", "重启通知", "boolean"),
            _field("triggers.error", "错误通知", "boolean"),
            _field("templates.offlineTitle", "离线通知标题", "text", advanced=True),
            _field("templates.offlineContent", "离线通知内容", "textarea", advanced=True),
            _field("templates.reconnectTitle", "重连通知标题", "text", advanced=True),
            _field("templates.reconnectContent", "重连通知内容", "textarea", advanced=True),
            _field("templates.restartTitle", "重启通知标题", "text", advanced=True),
            _field("templates.restartContent", "重启通知内容", "textarea", advanced=True),
        ],
        icon="bi-bell",
    ),
]


MESSAGE_FILTER_KEYS = ("ignore-mode", "whitelist", "blacklist")
MESSAGE_FILTER_CANDIDATES = ("XYBot", "AutoRestart", "")


def get_project_root() -> Path:
    return PROJECT_ROOT


def get_main_config_path() -> Path:
    return MAIN_CONFIG_PATH


def _is_table_like(value: Any) -> bool:
    return isinstance(value, (dict, Table))


def _to_plain(value: Any) -> Any:
    if isinstance(value, Bool):
        return bool(value)
    if isinstance(value, Integer):
        return int(value)
    if isinstance(value, Float):
        return float(value)
    if isinstance(value, AoT):
        return [_to_plain(item) for item in value]
    if isinstance(value, Array):
        return [_to_plain(item) for item in value]
    if isinstance(value, Table) or isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, Item):
        try:
            return value.unwrap()  # type: ignore[attr-defined]
        except Exception:
            return value.value if hasattr(value, "value") else str(value)
    return value


def _read_toml_document(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return tomlkit.load(f)


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{stamp}")
    try:
        shutil.copy2(path, backup)
        latest = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, latest)
        return backup
    except Exception as exc:
        logger.warning(f"备份配置失败 {path}: {exc}")
        return None


def _write_toml_document(path: Path, document, *, backup: bool = True) -> Optional[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_file(path) if backup and path.exists() else None
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            tomlkit.dump(document, f)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return backup_path


def _ensure_table(document, section: str):
    if section in document and _is_table_like(document[section]):
        return document[section]
    table = tomlkit.table()
    document[section] = table
    return document[section]


def _get_path(container: Any, path: str, default: Any = None) -> Any:
    if not path:
        return container
    current = container
    for part in path.split("."):
        if not _is_table_like(current) or part not in current:
            return default
        current = current[part]
    return current


def _set_path(container: Any, path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        raise ValueError("空路径无法写入")
    current = container
    for part in parts[:-1]:
        if part not in current or not _is_table_like(current[part]):
            current[part] = tomlkit.table()
        current = current[part]
    current[parts[-1]] = value


def _resolve_message_filter_owner(document) -> str:
    for owner in MESSAGE_FILTER_CANDIDATES:
        container = document if owner == "" else document.get(owner)
        if not _is_table_like(container):
            continue
        if any(key in container for key in MESSAGE_FILTER_KEYS):
            return owner
    return "XYBot"


def _get_message_filter_values(document) -> Dict[str, Any]:
    owner = _resolve_message_filter_owner(document)
    container = document if owner == "" else document.get(owner, {})
    values: Dict[str, Any] = {
        "ignore-mode": "None",
        "whitelist": [],
        "blacklist": [],
    }
    if not _is_table_like(container):
        return values
    for key in MESSAGE_FILTER_KEYS:
        if key in container:
            values[key] = _to_plain(container[key])
    return values


def _set_message_filter_values(document, values: Dict[str, Any]) -> None:
    owner = _resolve_message_filter_owner(document)
    preferred_owner = "XYBot"
    if owner and owner != preferred_owner:
        old_container = document if owner == "" else document.get(owner)
        if _is_table_like(old_container):
            for key in MESSAGE_FILTER_KEYS:
                if key in old_container:
                    del old_container[key]
    target = _ensure_table(document, preferred_owner)
    for key in MESSAGE_FILTER_KEYS:
        if key in values:
            target[key] = values[key]


def _default_for_field(field: Dict[str, Any]) -> Any:
    if "default" in field:
        return deepcopy(field["default"])
    field_type = field.get("type")
    if field_type == "boolean":
        return False
    if field_type == "number":
        return 0
    if field_type == "list":
        return []
    if field_type == "object":
        return {}
    return ""


def _normalize_list_items(items: Sequence[Any]) -> List[str]:
    result: List[str] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        # 兼容历史脏数据：列表里还嵌着 "['a', 'b']" 这种字符串
        if len(items) == 1 and (
            (text.startswith("[") and text.endswith("]"))
            or (text.startswith("(") and text.endswith(")"))
        ):
            nested = _parse_list_value(text)
            if nested:
                return nested
        result.append(text)
    return result


def _parse_list_value(value: Any) -> List[str]:
    """把 list / 逗号串 / Python 风格字符串列表 统一成字符串数组。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _normalize_list_items(list(value))
    if not isinstance(value, str):
        text = str(value).strip()
        return [text] if text else []

    text = value.strip()
    if not text:
        return []

    # 历史配置常把 TOML 数组写成字符串：admins = "['a', 'b']"
    if (text.startswith("[") and text.endswith("]")) or (
        text.startswith("(") and text.endswith(")")
    ):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (list, tuple, set)):
                return _normalize_list_items(list(parsed))
        except Exception:
            pass
        try:
            import json

            parsed = json.loads(text.replace("'", '"'))
            if isinstance(parsed, list):
                return _normalize_list_items(parsed)
        except Exception:
            pass

    return [item.strip() for item in re.split(r"[\n,]", text) if item.strip()]


def _coerce_value(field: Dict[str, Any], value: Any) -> Any:
    field_type = field.get("type")
    if field_type == "boolean":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if field_type == "number":
        if value is None or value == "":
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        return float(text)
    if field_type == "list":
        return _parse_list_value(value)
    if field_type == "select":
        return "" if value is None else str(value)
    if field_type in {"text", "password", "textarea"}:
        return "" if value is None else str(value)
    return value


def _extract_section_values(document, section_key: str, fields: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if section_key == "MessageFilter":
        raw = _get_message_filter_values(document)
        return {
            field["key"]: _coerce_value(field, raw.get(field["key"], _default_for_field(field)))
            for field in fields
        }

    values: Dict[str, Any] = {}
    section = document.get(section_key, {})
    for field in fields:
        key = field["key"]
        raw = _get_path(section, key, None) if _is_table_like(section) else None
        if raw is None:
            values[key] = _default_for_field(field)
        else:
            values[key] = _coerce_value(field, _to_plain(raw))
    return values


def _apply_section_values(
    document,
    section_key: str,
    fields: Sequence[Dict[str, Any]],
    payload: Dict[str, Any],
) -> None:
    if section_key == "MessageFilter":
        coerced = {
            field["key"]: _coerce_value(field, payload.get(field["key"], _default_for_field(field)))
            for field in fields
        }
        _set_message_filter_values(document, coerced)
        return

    section = _ensure_table(document, section_key)
    for field in fields:
        key = field["key"]
        if field.get("readonly"):
            continue
        if key not in payload:
            continue
        value = _coerce_value(field, payload[key])
        _set_path(section, key, value)


def get_main_config_schema() -> List[Dict[str, Any]]:
    return deepcopy(MAIN_CONFIG_SCHEMA)


def load_main_config_view(path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = Path(path) if path else MAIN_CONFIG_PATH
    document = _read_toml_document(config_path)
    values: Dict[str, Any] = {}
    for section in MAIN_CONFIG_SCHEMA:
        values[section["key"]] = _extract_section_values(document, section["key"], section["fields"])
    return {
        "path": str(config_path),
        "schema": get_main_config_schema(),
        "values": values,
        "raw": config_path.read_text(encoding="utf-8"),
    }


def save_main_config_values(payload: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = Path(path) if path else MAIN_CONFIG_PATH
    document = _read_toml_document(config_path)
    section_map = {section["key"]: section for section in MAIN_CONFIG_SCHEMA}

    if not isinstance(payload, dict):
        raise ValueError("配置数据必须是对象")

    for section_key, section_values in payload.items():
        section = section_map.get(section_key)
        if not section:
            if isinstance(section_values, dict):
                target = _ensure_table(document, section_key)
                for key, value in section_values.items():
                    target[key] = value
            continue
        if not isinstance(section_values, dict):
            raise ValueError(f"配置段 {section_key} 必须是对象")
        _apply_section_values(document, section_key, section["fields"], section_values)

    backup_path = _write_toml_document(config_path, document, backup=True)
    return {
        "path": str(config_path),
        "backup": str(backup_path) if backup_path else None,
        "values": load_main_config_view(config_path)["values"],
    }


def save_main_config_raw(content: str, path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = Path(path) if path else MAIN_CONFIG_PATH
    text = content if content.endswith("\n") else content + "\n"
    tomlkit.parse(text)
    backup_path = _backup_file(config_path) if config_path.exists() else None
    with open(config_path, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(text)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {
        "path": str(config_path),
        "backup": str(backup_path) if backup_path else None,
    }


_KEY_COMMENT_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=\s*.*?#\s*(.+?)\s*$")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _infer_field_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return "object_list"
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str) and ("\n" in value or len(value) > 120):
        return "textarea"
    return "text"


def _humanize_key(key: str) -> str:
    text = key.replace("-", " ").replace("_", " ").strip()
    return text if text else key


def _collect_inline_comments(path: Path) -> Dict[str, str]:
    comments: Dict[str, str] = {}
    if not path.exists():
        return comments
    current_section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        section_match = _SECTION_RE.match(raw)
        if section_match:
            current_section = section_match.group(1).strip()
            continue
        key_match = _KEY_COMMENT_RE.match(raw)
        if not key_match:
            continue
        key = key_match.group(1)
        comment = key_match.group(2)
        full_key = f"{current_section}.{key}" if current_section else key
        comments[full_key] = comment
    return comments


def _build_fields_from_value(
    prefix: str,
    value: Any,
    comments: Dict[str, str],
    *,
    max_depth: int = 3,
    depth: int = 0,
    root_prefix: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从 dict 推断字段；嵌套对象展平为相对 section 的点号路径。"""
    fields: List[Dict[str, Any]] = []
    if not isinstance(value, dict):
        return fields

    section_prefix = prefix if root_prefix is None else root_prefix

    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        field_type = _infer_field_type(item)
        if field_type == "object" and depth < max_depth:
            nested = _build_fields_from_value(
                path,
                item,
                comments,
                max_depth=max_depth,
                depth=depth + 1,
                root_prefix=section_prefix,
            )
            fields.extend(nested)
            continue
        if field_type == "object_list":
            continue
        secret = any(
            token in str(key).lower()
            for token in ("password", "token", "secret", "api-key", "apikey", "admin-key")
        )
        # 始终相对顶层 section，避免 nested.host 被收成 host
        relative_key = path if not section_prefix else path[len(section_prefix) + 1 :]
        fields.append(
            _field(
                relative_key,
                _humanize_key(str(key)),
                "password" if secret and field_type == "text" else field_type,
                description=comments.get(path, ""),
                secret=secret,
            )
        )
    return fields


def infer_schema_from_toml(path: Path, title: str = "配置") -> List[Dict[str, Any]]:
    document = _read_toml_document(path)
    plain = _to_plain(document)
    comments = _collect_inline_comments(path)
    sections: List[Dict[str, Any]] = []

    if not isinstance(plain, dict):
        return sections

    for section_key, section_value in plain.items():
        if not isinstance(section_value, dict):
            continue
        fields = _build_fields_from_value(str(section_key), section_value, comments)
        if not fields:
            continue
        sections.append(
            _section(
                str(section_key),
                _humanize_key(str(section_key)),
                f"{title} / {section_key}",
                fields,
                icon="bi-sliders",
            )
        )
    return sections


def load_generic_config_view(path: Path, title: str = "配置") -> Dict[str, Any]:
    document = _read_toml_document(path)
    schema = infer_schema_from_toml(path, title=title)
    values: Dict[str, Any] = {}
    for section in schema:
        section_data = document.get(section["key"], {})
        values[section["key"]] = {}
        for field in section["fields"]:
            raw = (
                _get_path(section_data, field["key"], None)
                if _is_table_like(section_data)
                else None
            )
            values[section["key"]][field["key"]] = (
                _default_for_field(field)
                if raw is None
                else _coerce_value(field, _to_plain(raw))
            )
    return {
        "path": str(path),
        "schema": schema,
        "values": values,
        "raw": path.read_text(encoding="utf-8"),
    }


def save_generic_config_values(path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    document = _read_toml_document(path)
    if not isinstance(payload, dict):
        raise ValueError("配置数据必须是对象")

    for section_key, section_values in payload.items():
        if not isinstance(section_values, dict):
            continue
        section = _ensure_table(document, section_key)
        for key, value in section_values.items():
            _set_path(section, str(key), value)

    backup_path = _write_toml_document(path, document, backup=True)
    return {
        "path": str(path),
        "backup": str(backup_path) if backup_path else None,
        "values": load_generic_config_view(path)["values"],
    }


def save_generic_config_raw(path: Path, content: str) -> Dict[str, Any]:
    text = content if content.endswith("\n") else content + "\n"
    tomlkit.parse(text)
    backup_path = _backup_file(path) if path.exists() else None
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.seek(0)
            f.truncate()
            f.write(text)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return {
        "path": str(path),
        "backup": str(backup_path) if backup_path else None,
    }
