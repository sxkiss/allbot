"""
@input: tomlkit、main_config.toml / 插件与适配器 config.toml、字段 schema 元数据
@output: 可视化配置 schema、结构化读写与保留注释的 TOML 更新能力
@position: 管理后台配置服务层，把原始 TOML 转成普通用户可编辑的表单数据
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import ast
import contextvars
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
    item_default: Any = None,
    item_fields: Optional[List[Dict[str, Any]]] = None,
    key_field: str = "",
    key_label: str = "",
    add_label: str = "",
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
    if item_default is not None:
        data["item_default"] = item_default
    if item_fields is not None:
        data["item_fields"] = item_fields
    if key_field:
        data["key_field"] = key_field
    if key_label:
        data["key_label"] = key_label
    if add_label:
        data["add_label"] = add_label
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
        "WechatAPIServer",
        "消息总线",
        "Redis / 消息消费者相关设置（协议连接已由适配器管理）。",
        [
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
            _field("enable-rabbitmq", "启用 RabbitMQ 收消息", "boolean", advanced=True),
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
        "AllBot",
        "机器人核心",
        "机器人身份、管理员、数据库与常用运行开关。",
        [
            _field("version", "版本号", "text", readonly=True, description="框架版本，请勿修改。"),
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
            _field("AllBotDB-url", "主数据库地址", "text", advanced=True),
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

]


MESSAGE_FILTER_KEYS = ("ignore-mode", "whitelist", "blacklist")
MESSAGE_FILTER_CANDIDATES = ("AllBot", "AutoRestart", "")


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
    return "AllBot"


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
    preferred_owner = "AllBot"
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
    if field_type in {"list", "object_list"}:
        return []
    if field_type in {"object", "object_map"}:
        return {}
    return ""


def _item_default_for_field(field: Dict[str, Any]) -> Dict[str, Any]:
    """多账号卡片新增项的默认值；与字段本身的 empty default 区分开。"""
    item_default = field.get("item_default")
    if isinstance(item_default, dict):
        return deepcopy(item_default)
    default = field.get("default")
    # 兼容旧 schema：曾把 item_defaults 误放到 field.default
    if isinstance(default, dict) and field.get("type") in {"object_list", "object_map"}:
        return deepcopy(default)
    return {}


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
    if field_type == "object_list":
        return _coerce_object_list(field, value)
    if field_type == "object_map":
        return _coerce_object_map(field, value)
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


_MULTI_ACCOUNT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "telegram.bots": {
        "type": "object_list",
        "label": "多机器人",
        "description": "配置后会忽略上方单个 token，并为每个 bot 单独启动轮询。",
        "item_label": "机器人",
        "add_label": "添加机器人",
        "item_fields": [
            {"key": "name", "label": "名称", "type": "text", "placeholder": "support", "description": "会进入会话 ID，例如 telegram-support-123"},
            {"key": "token", "label": "Bot Token", "type": "password", "secret": True, "placeholder": "123456:ABC..."},
            {"key": "polling", "label": "启用轮询", "type": "boolean", "default": True},
            {"key": "proxyHost", "label": "反代地址", "type": "text", "placeholder": "https://tg.example.com", "advanced": True},
            {"key": "httpProxy", "label": "HTTP 代理", "type": "text", "placeholder": "http://127.0.0.1:7890", "advanced": True},
        ],
        "item_defaults": {
            "name": "",
            "token": "",
            "polling": True,
            "proxyHost": "",
            "httpProxy": "",
        },
    },
    "wecom_bot.bots": {
        "type": "object_map",
        "label": "多机器人",
        "description": "每个 bot 需要企业微信智能机器人的 botId 与 secret。",
        "item_label": "机器人",
        "add_label": "添加机器人",
        "key_field": "name",
        "key_label": "机器人标识",
        "item_fields": [
            {"key": "name", "label": "机器人标识", "type": "text", "placeholder": "default", "description": "配置键名，例如 default / support"},
            {"key": "botId", "label": "Bot ID", "type": "text", "placeholder": "aibot_xxx"},
            {"key": "secret", "label": "Secret", "type": "password", "secret": True},
        ],
        "item_defaults": {
            "name": "default",
            "botId": "",
            "secret": "",
        },
    },
    "ocwx.accounts": {
        "type": "object_map",
        "label": "多账号槽位",
        "description": "每个槽位对应一个 OpenClaw 微信账号，可独立启用。",
        "item_label": "账号",
        "add_label": "添加账号",
        "key_field": "slot",
        "key_label": "槽位名",
        "item_fields": [
            {"key": "slot", "label": "槽位名", "type": "text", "placeholder": "main", "description": "配置键名，例如 main / work"},
            {"key": "enabled", "label": "启用", "type": "boolean", "default": True},
            {"key": "displayName", "label": "显示名", "type": "text", "placeholder": "主号"},
            {"key": "baseUrl", "label": "接口地址", "type": "text", "placeholder": "https://ilinkai.weixin.qq.com", "advanced": True},
            {"key": "cdnBaseUrl", "label": "CDN 地址", "type": "text", "advanced": True},
            {"key": "pollTimeoutMs", "label": "轮询超时(ms)", "type": "number", "advanced": True},
            {"key": "requestTimeoutMs", "label": "请求超时(ms)", "type": "number", "advanced": True},
        ],
        "item_defaults": {
            "slot": "main",
            "enabled": True,
            "displayName": "主号",
            "baseUrl": "",
            "cdnBaseUrl": "",
            "pollTimeoutMs": 0,
            "requestTimeoutMs": 0,
        },
    },
}


def _is_secret_key(key: str) -> bool:
    lower = str(key).lower()
    return any(token in lower for token in ("password", "token", "secret", "api-key", "apikey"))


def _field_from_sample(key: str, sample: Any = None, *, description: str = "") -> Dict[str, Any]:
    field_type = _infer_field_type(sample) if sample is not None else "text"
    if field_type in {"object", "object_list", "object_map"}:
        field_type = "text"
    secret = _is_secret_key(str(key))
    return _field(
        str(key),
        _zh_label(str(key)),
        "password" if secret and field_type == "text" else field_type,
        description=description,
        secret=secret,
        default=sample if sample is not None else _default_for_field({"type": field_type}),
    )


def _build_item_fields_from_samples(samples: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    keys: List[str] = []
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        for key in sample.keys():
            key_text = str(key)
            if key_text in seen:
                continue
            seen.add(key_text)
            keys.append(key_text)
    fields: List[Dict[str, Any]] = []
    for key in keys:
        sample_value = None
        for sample in samples:
            if isinstance(sample, dict) and key in sample:
                sample_value = sample.get(key)
                break
        fields.append(_field_from_sample(key, sample_value))
    return fields


def _resolve_multi_account_template(section_key: str, field_key: str) -> Optional[Dict[str, Any]]:
    return _MULTI_ACCOUNT_TEMPLATES.get(f"{section_key}.{field_key}")


def _normalize_item_fields(item_fields: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in item_fields:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        field = _field(
            str(item["key"]),
            str(item.get("label") or _zh_label(str(item["key"]))),
            str(item.get("type") or "text"),
            description=str(item.get("description") or ""),
            placeholder=str(item.get("placeholder") or ""),
            secret=bool(item.get("secret")),
            advanced=bool(item.get("advanced")),
            readonly=bool(item.get("readonly")),
            unit=str(item.get("unit") or ""),
            default=item.get("default"),
            item_label=str(item.get("item_label") or "项"),
        )
        result.append(field)
    return result


def _build_multi_account_field(
    section_key: str,
    field_key: str,
    raw_value: Any,
    *,
    description: str = "",
) -> Optional[Dict[str, Any]]:
    template = _resolve_multi_account_template(section_key, field_key)
    preferred_type = None
    if template:
        preferred_type = template.get("type")
    elif isinstance(raw_value, list):
        preferred_type = "object_list"
    elif isinstance(raw_value, dict):
        # only treat known multi-account containers or homogeneous nested dicts as object_map
        if field_key in {"bots", "accounts"}:
            preferred_type = "object_map"
        elif raw_value and all(isinstance(v, dict) for v in raw_value.values()):
            preferred_type = "object_map"
        else:
            return None
    else:
        return None

    if preferred_type == "object_list":
        items = raw_value if isinstance(raw_value, list) else []
        samples = [item for item in items if isinstance(item, dict)]
        item_fields = _normalize_item_fields(template.get("item_fields") or []) if template else []
        if not item_fields:
            item_fields = _build_item_fields_from_samples(samples)
        if not item_fields and template and template.get("item_defaults"):
            item_fields = _build_item_fields_from_samples([template["item_defaults"]])
        if not item_fields:
            return None
        return _field(
            field_key,
            str((template or {}).get("label") or _zh_label(field_key)),
            "object_list",
            description=str((template or {}).get("description") or description or ""),
            item_label=str((template or {}).get("item_label") or "项"),
            add_label=str((template or {}).get("add_label") or ""),
            item_fields=item_fields,
            default=[],
            item_default=deepcopy((template or {}).get("item_defaults") or {}),
        )

    if preferred_type == "object_map":
        mapping = raw_value if isinstance(raw_value, dict) else {}
        samples = []
        for name, payload in mapping.items():
            if not isinstance(payload, dict):
                continue
            sample = dict(payload)
            key_field = str((template or {}).get("key_field") or "name")
            if key_field not in sample:
                sample[key_field] = str(name)
            samples.append(sample)
        item_fields = _normalize_item_fields(template.get("item_fields") or []) if template else []
        if not item_fields:
            item_fields = _build_item_fields_from_samples(samples)
        if not item_fields and template and template.get("item_defaults"):
            item_fields = _build_item_fields_from_samples([template["item_defaults"]])
        if not item_fields:
            return None
        return _field(
            field_key,
            str((template or {}).get("label") or _zh_label(field_key)),
            "object_map",
            description=str((template or {}).get("description") or description or ""),
            item_label=str((template or {}).get("item_label") or "项"),
            add_label=str((template or {}).get("add_label") or ""),
            key_field=str((template or {}).get("key_field") or "name"),
            key_label=str((template or {}).get("key_label") or "标识"),
            item_fields=item_fields,
            default={},
            item_default=deepcopy((template or {}).get("item_defaults") or {}),
        )
    return None


def _coerce_object_item(item_fields: Sequence[Dict[str, Any]], value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    result: Dict[str, Any] = {}
    for field in item_fields:
        key = field["key"]
        if key in raw:
            result[key] = _coerce_value(field, raw.get(key))
        else:
            result[key] = _default_for_field(field)
    return result


def _coerce_object_list(field: Dict[str, Any], value: Any) -> List[Dict[str, Any]]:
    item_fields = field.get("item_fields") or []
    if not isinstance(value, list):
        return []
    return [
        _coerce_object_item(item_fields, item)
        for item in value
        if isinstance(item, dict)
    ]


def _coerce_object_map(field: Dict[str, Any], value: Any) -> Dict[str, Any]:
    item_fields = field.get("item_fields") or []
    key_field = str(field.get("key_field") or "name")
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Any] = {}
    for name, payload in value.items():
        if not isinstance(payload, dict):
            continue
        item = _coerce_object_item(item_fields, payload)
        key_value = str(item.get(key_field) or name).strip() or str(name)
        item[key_field] = key_value
        result[key_value] = item
    return result


def _object_map_to_toml_table(field: Dict[str, Any], value: Any):
    item_fields = field.get("item_fields") or []
    key_field = str(field.get("key_field") or "name")
    mapping = value if isinstance(value, dict) else {}
    table = tomlkit.table()
    for name, payload in mapping.items():
        if not isinstance(payload, dict):
            continue
        item = _coerce_object_item(item_fields, payload)
        key_value = str(item.get(key_field) or name).strip() or str(name)
        child = tomlkit.table()
        for item_field in item_fields:
            item_key = item_field["key"]
            if item_key == key_field:
                continue
            if item_key not in item:
                continue
            child[item_key] = item[item_key]
        table[key_value] = child
    return table


def _object_list_to_toml_array(field: Dict[str, Any], value: Any):
    item_fields = field.get("item_fields") or []
    items = value if isinstance(value, list) else []
    array = tomlkit.aot()
    for payload in items:
        if not isinstance(payload, dict):
            continue
        item = _coerce_object_item(item_fields, payload)
        child = tomlkit.table()
        for item_field in item_fields:
            item_key = item_field["key"]
            if item_key not in item:
                continue
            child[item_key] = item[item_key]
        array.append(child)
    return array


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


# 可视化中文字段释义仅对「适配器配置」生效：插件配置同样走
# load_generic_config_view，但其字段语义不同（如 enable 是插件自身开关，
# 与 [adapter].enabled 无关），全局套用会产出指错门的说明。
# 用请求级 ContextVar 限定作用域，避免改动一串函数签名。
_ZH_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar("config_zh_scope", default="adapter")


def _is_adapter_config_path(path: Path) -> bool:
    """按配置文件路径判断是否为适配器配置（plugins/ 一律视为插件）。"""
    parts = set(Path(path).parts)
    return "adapter" in parts and "plugins" not in parts


# ---------------------------------------------------------------------------
# 可视化表单中文字段释义（集中维护，覆盖适配器配置常见键）
# label = 中文名；desc = 该字段的用途说明（在表单里作为 form-text 显示）。
# 优先级：config.toml 行内注释 > 本字典 > 英文键名。
# ---------------------------------------------------------------------------
_FIELD_ZH: Dict[str, Dict[str, str]] = {
    # [adapter] 通用段
    "name": {"label": "名称", "desc": "配置项名称标识。"},
    "enabled": {"label": "启用", "desc": "是否启用该模块。"},
    "module": {"label": "模块路径", "desc": "适配器 Python 模块导入路径，框架据此加载类。"},
    "class": {"label": "适配器类", "desc": "适配器实现类名，由 module 导入。"},
    "replyQueue": {"label": "回复队列", "desc": "出站回复的 Redis 队列名，ReplyDispatcher 按 platform 分发到这里。"},
    "replyMaxRetry": {"label": "回复最大重试", "desc": "发送失败时的最多重试次数。"},
    "replyRetryInterval": {"label": "回复重试间隔(秒)", "desc": "两次重试之间的等待秒数。"},
    "logEnabled": {"label": "启用日志", "desc": "是否输出该适配器运行日志。"},
    "logLevel": {"label": "日志级别", "desc": "DEBUG / INFO / WARNING / ERROR，排查问题时可临时调为 DEBUG。"},
    "enable": {"label": "启用", "desc": "是否启用该功能；适配器需 [adapter].enabled 与平台段同时开启（双开关）。"},
    "platform": {"label": "平台标识", "desc": "消息路由使用的平台名，一般不要修改。"},
    "aliases": {"label": "平台别名", "desc": "兼容旧配置的等价平台名列表。"},
    # 网络 / 网关
    "host": {"label": "地址", "desc": "服务或 Redis 主机地址。"},
    "port": {"label": "端口", "desc": "服务或 Redis 端口。"},
    "baseUrl": {"label": "协议服务地址", "desc": "协议网关 HTTP 根地址。"},
    "wsUrl": {"label": "WebSocket 地址", "desc": "协议推送的 WS 地址（收消息链路）。"},
    "sendUrl": {"label": "发送接口地址", "desc": "出站消息发送端点。"},
    "staticBaseUrl": {"label": "静态资源地址", "desc": "媒体/静态文件访问根地址。"},
    "apiPrefix": {"label": "API 前缀", "desc": "协议服务接口统一前缀。"},
    "url": {"label": "地址", "desc": "Webhook 或服务的 URL。"},
    "listenHost": {"label": "监听地址", "desc": "Webhook 监听的主机地址。"},
    "listenPort": {"label": "监听端口", "desc": "Webhook 监听端口。"},
    "httpProxy": {"label": "HTTP 代理", "desc": "留空直连；形如 http://127.0.0.1:7890。"},
    "proxyHost": {"label": "反代地址", "desc": "Telegram 等平台的反向代理地址，留空直连。"},
    "protocolVersion": {"label": "协议版本", "desc": "协议网关版本标识，如 869 / 875，勿随意修改。"},
    # 凭据 / 鉴权
    "token": {"label": "访问令牌", "desc": "平台 Bot Token / 访问凭据，请保密。"},
    "apiKey": {"label": "API 密钥", "desc": "调用协议服务的密钥，请保密。"},
    "adminKey": {"label": "管理密钥", "desc": "协议服务 adminKey；留空时由会话接管提供。"},
    "authcode": {"label": "卡密", "desc": "一卡密对应一个微信账号。请保密。"},
    "botId": {"label": "机器人 ID", "desc": "平台分配的机器人标识。"},
    "secret": {"label": "密钥", "desc": "平台分配的 Secret，请保密。"},
    "secretToken": {"label": "Webhook 密钥", "desc": "校验 Webhook 回调的签名密钥。"},
    "sessionId": {"label": "会话 ID", "desc": "协议侧会话标识。"},
    "sessionFile": {"label": "会话文件", "desc": "登录态落盘路径（该账户独立文件）。"},
    "stateFile": {"label": "状态文件", "desc": "适配器运行状态落盘路径。"},
    "statePath": {"label": "状态路径", "desc": "同步游标/状态文件所在路径。"},
    # Redis
    "db": {"label": "Redis 库号", "desc": "Redis 数据库编号，默认 0。"},
    "password": {"label": "密码", "desc": "Redis 或平台密码；无密码留空。"},
    "queue": {"label": "入站队列", "desc": "适配器把消息写入的 Redis 主队列，默认 allbot。"},
    # 行为 / 开关
    "polling": {"label": "启用轮询", "desc": "是否以长轮询方式拉取消息。"},
    "pollingTimeout": {"label": "轮询超时(秒)", "desc": "单次长轮询等待上限。"},
    "pollingInterval": {"label": "轮询间隔(秒)", "desc": "两次轮询之间的间隔。"},
    "pollingLimit": {"label": "单轮拉取条数", "desc": "一次轮询最多取回的消息数。"},
    "enableWebSocket": {"label": "启用 WebSocket", "desc": "是否使用 WS 推送接收消息。"},
    "webSocketReplay": {"label": "WS 历史回放", "desc": "WS 连接后是否回放历史消息。"},
    "skipHistoryOnStart": {"label": "启动跳过历史", "desc": "启动时不补拉历史消息，避免重复处理。"},
    "startupSyncLimit": {"label": "启动同步上限", "desc": "启动时最多同步的历史消息条数。"},
    "startupSyncMaxPages": {"label": "启动同步最大页数", "desc": "启动时历史同步最多翻页数。"},
    "includeSent": {"label": "包含自发消息", "desc": "同步时是否纳入自己发出的消息（谨慎开启，易与出站回环混淆）。"},
    "dropPendingUpdates": {"label": "丢弃待处理更新", "desc": "启动时忽略积压的旧更新。"},
    "allowPrivate": {"label": "允许私聊", "desc": "是否处理私聊消息。"},
    "allowGroups": {"label": "允许群聊", "desc": "是否处理群聊消息。"},
    "allowedGroups": {"label": "群白名单", "desc": "仅处理这些群 ID，留空表示不限制。"},
    "groupWhitelist": {"label": "群白名单", "desc": "仅处理这些群 ID，留空表示不限制。"},
    "groupMessagesAsAt": {"label": "群消息转为 @", "desc": "群消息回复时自动 @ 发送者。"},
    "autoWelcome": {"label": "自动欢迎", "desc": "新成员/新会话时自动发送欢迎语。"},
    "welcomeText": {"label": "欢迎语", "desc": "自动欢迎使用的文本内容。"},
    "replyWithStream": {"label": "流式回复", "desc": "以流式消息方式回复。"},
    "defaultMarkdownMode": {"label": "默认 Markdown 模式", "desc": "回复默认使用的 Markdown 渲染方式。"},
    "fallbackUnsupportedToText": {"label": "不支持类型转文本", "desc": "平台不支持的消息类型降级为纯文本。"},
    "logRawMessage": {"label": "记录原始消息", "desc": "在日志里打印未归一化的原始报文，排障用。"},
    "sessionTakeover": {"label": "会话接管", "desc": "启动时接管已有登录会话，避免重复扫码。"},
    "loginAutoPoll": {"label": "登录自动轮询", "desc": "扫码后自动轮询登录状态。"},
    "loginCheckInterval": {"label": "登录检查间隔(秒)", "desc": "登录状态轮询间隔。"},
    "qrRefreshInterval": {"label": "二维码刷新间隔(秒)", "desc": "二维码自动刷新周期。"},
    "qrSavePath": {"label": "二维码保存路径", "desc": "二维码图片落盘位置。"},
    "botWxid": {"label": "机器人 wxid", "desc": "机器人自身 wxid，用于识别自发消息。"},
    "selfId": {"label": "自身 ID", "desc": "当前账号标识，用于过滤自发消息。"},
    "ignoreGhId": {"label": "忽略公众号", "desc": "忽略公众号(gh_)消息。"},
    "mediaCacheDir": {"label": "媒体缓存目录", "desc": "下载/上传媒体的本地缓存路径。"},
    "dedupCacheSize": {"label": "去重缓存容量", "desc": "消息去重 LRU 缓存条数。"},
    "maxClients": {"label": "最大客户端数", "desc": "同时接受的连接数上限。"},
    "debug": {"label": "调试模式", "desc": "输出更详细日志，生产建议关闭。"},
    "disableSend": {"label": "禁用发送", "desc": "只接收不发送，用于观察/演练。"},
    # 重试 / 超时 / 限流
    "requestTimeout": {"label": "请求超时(秒)", "desc": "HTTP 请求超时秒数。"},
    "reconnectInterval": {"label": "重连间隔(秒)", "desc": "连接断开后重连等待秒数。"},
    "reconnectDelay": {"label": "重连延迟(秒)", "desc": "重连前的延迟秒数。"},
    "pingInterval": {"label": "心跳间隔(秒)", "desc": "WS 心跳发送间隔。"},
    "cmdTimeout": {"label": "指令超时(秒)", "desc": "等待指令响应的超时秒数。"},
    "downloadRetry": {"label": "下载重试", "desc": "媒体下载失败时的重试次数。"},
    "initRetry": {"label": "初始化重试", "desc": "启动初始化失败时的重试次数。"},
    "initRetryInterval": {"label": "初始化重试间隔(秒)", "desc": "初始化重试之间的等待秒数。"},
    "rateLimitPerMinute": {"label": "每分钟限流", "desc": "每分钟最多发送消息数，0 不限。"},
    "rateLimitPerHour": {"label": "每小时限流", "desc": "每小时最多发送消息数，0 不限。"},
    "outboxPollTimeout": {"label": "发件箱超时(秒)", "desc": "发件箱轮询超时。"},
    "outboxPollInterval": {"label": "发件箱间隔(秒)", "desc": "发件箱轮询间隔。"},
    # 设备 / 多账户
    "deviceName": {"label": "登录设备类型", "desc": "如 ipad / mac / win；875 按设备区分拉码接口。"},
    "deviceId": {"label": "设备 ID", "desc": "设备唯一标识，留空自动生成。"},
    "default_device": {"label": "默认设备", "desc": "登录弹窗默认选中的设备类型。"},
    "defaultBot": {"label": "默认机器人", "desc": "未指定时使用的机器人配置键。"},
    "slot": {"label": "槽位名", "desc": "多账户配置键名，如 main / work。"},
    "displayName": {"label": "显示名", "desc": "后台账号下拉里显示的名称。"},
    # section 段名
    # 补充：平台段名与剩余字段
    "ocwx": {"label": "ocwx 渠道", "desc": "微信 clawbot 渠道（OpenClaw 微信）配置。"},
    "qq": {"label": "QQ 平台", "desc": "QQ 适配器专属配置。"},
    "telegram": {"label": "Telegram 平台", "desc": "Telegram Bot 专属配置。"},
    "web": {"label": "Web 对话", "desc": "管理后台 Web 对话适配器配置。"},
    "wechat869": {"label": "869 协议", "desc": "869 协议网关连接与会话配置。"},
    "wechat875": {"label": "875 协议", "desc": "875 协议网关连接与设备配置。"},
    "wechat_observatory": {"label": "微信观测台", "desc": "WeChat Observatory 公共 API 桥接配置。"},
    "wecom_bot": {"label": "企微智能机器人", "desc": "企业微信 aibot 长连接配置。"},
    "win": {"label": "Win 协议", "desc": "Windows 协议桥接配置。"},
    "wxfilehelper": {"label": "文件助手", "desc": "wx-filehelper-api 协议桥接配置。"},
    "adapter": {"label": "适配器基础配置", "desc": "所有适配器共用的启停、模块与队列设置。"},
    "redis": {"label": "Redis 连接", "desc": "消息队列使用的 Redis 连接信息。"},
    "accounts": {"label": "多账户", "desc": "一个卡密/账号对应一个实例，多设备可并行在线。"},
    "bots": {"label": "多机器人", "desc": "多个 Bot 实例并行运行。"},
}


def _zh_label(key: str) -> str:
    """字段中文标签：仅适配器作用域生效；插件/其他配置回退英文键名。"""
    if _ZH_SCOPE.get() != "adapter":
        return _humanize_key(key)
    item = _FIELD_ZH.get(key)
    return (item or {}).get("label") or _humanize_key(key)


def _zh_desc(key: str, inline_comment: str) -> str:
    """字段说明：行内注释永远优先；适配器作用域下才补字典释义；否则返回空串。

    空串前端不渲染 form-text（见 config_form.js 的 truthy 判断），无副作用。
    """
    if inline_comment:
        return inline_comment
    if _ZH_SCOPE.get() != "adapter":
        return ""
    item = _FIELD_ZH.get(key)
    return (item or {}).get("desc") or ""


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
    section_key = section_prefix.split(".", 1)[0] if section_prefix else ""

    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        relative_key = path if not section_prefix else path[len(section_prefix) + 1 :]
        # 行内注释优先，否则用集中式中文释义
        inline = comments.get(path, "")
        zh_desc = _zh_desc(str(key), inline)
        multi_field = None
        if depth == 0 and "." not in relative_key:
            multi_field = _build_multi_account_field(
                section_key,
                relative_key,
                item,
                description=zh_desc,
            )
        if multi_field is not None:
            fields.append(multi_field)
            continue

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
            multi_field = _build_multi_account_field(
                section_key,
                relative_key,
                item,
                description=zh_desc,
            )
            if multi_field is not None:
                fields.append(multi_field)
            continue
        secret = _is_secret_key(str(key))
        fields.append(
            _field(
                relative_key,
                _zh_label(str(key)),
                "password" if secret and field_type == "text" else field_type,
                description=zh_desc,
                secret=secret,
            )
        )
    return fields


def _inject_multi_account_templates(
    section_key: str,
    fields: List[Dict[str, Any]],
    section_value: Dict[str, Any],
    comments: Dict[str, str],
) -> List[Dict[str, Any]]:
    existing = {field["key"] for field in fields}
    for full_key, template in _MULTI_ACCOUNT_TEMPLATES.items():
        if not full_key.startswith(f"{section_key}."):
            continue
        field_key = full_key.split(".", 1)[1]
        if field_key in existing:
            continue
        multi_field = _build_multi_account_field(
            section_key,
            field_key,
            section_value.get(field_key),
            description=_zh_desc(field_key, comments.get(f"{section_key}.{field_key}", "")),
        )
        if multi_field is not None:
            fields.append(multi_field)
            existing.add(field_key)
    return fields


def infer_schema_from_toml(path: Path, title: str = "配置") -> List[Dict[str, Any]]:
    scope_token = _ZH_SCOPE.set("adapter" if _is_adapter_config_path(path) else "other")
    try:
        return _infer_schema_from_toml_inner(path, title)
    finally:
        _ZH_SCOPE.reset(scope_token)


def _infer_schema_from_toml_inner(path: Path, title: str) -> List[Dict[str, Any]]:
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
        fields = _inject_multi_account_templates(
            str(section_key),
            fields,
            section_value,
            comments,
        )
        if not fields:
            continue
        sections.append(
            _section(
                str(section_key),
                _zh_label(str(section_key)),
                _zh_desc(str(section_key), "") or f"{title} / {section_key}",
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

    schema = infer_schema_from_toml(path)
    field_map: Dict[str, Dict[str, Dict[str, Any]]] = {
        section["key"]: {field["key"]: field for field in section.get("fields") or []}
        for section in schema
    }

    for section_key, section_values in payload.items():
        if not isinstance(section_values, dict):
            continue
        section = _ensure_table(document, section_key)
        section_fields = field_map.get(section_key, {})
        for key, value in section_values.items():
            field = section_fields.get(str(key))
            if field and field.get("type") == "object_list":
                _set_path(section, str(key), _object_list_to_toml_array(field, value))
            elif field and field.get("type") == "object_map":
                _set_path(section, str(key), _object_map_to_toml_table(field, value))
            elif field:
                _set_path(section, str(key), _coerce_value(field, value))
            else:
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
