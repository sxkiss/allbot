"""
@input: WechatAPIClient 消息回调、插件配置、插件市场 API
@output: 依赖/插件安装与插件市场查询/提交响应；GitHub 插件目录名规范化为合法 Python 包名
@position: 插件管理辅助能力（依赖安装、插件安装、插件市场查询/提交）
@auto-doc: Update header and folder INDEX.md when this file changes

依赖包管理插件 - 允许管理员通过微信命令安装Python依赖包和Github插件

作者: 老夏的金库
版本: 1.1.0
"""
import asyncio
import json
import os
import sys
import subprocess
import tomllib
import importlib
import re
import shutil
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from loguru import logger
import requests
import zipfile
import io
from datetime import datetime

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class DependencyManager(PluginBase):
    """依赖包管理插件，允许管理员通过微信发送命令来安装/更新/查询Python依赖包和Github插件"""
    
    description = "依赖包管理插件"
    author = "老夏的金库"
    version = "1.1.0"
    
    def __init__(self):
        super().__init__()
        
        # 记录插件开始初始化
        logger.critical("[DependencyManager] 开始加载插件")
        
        # 获取配置文件路径
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(self.plugin_dir, "config.toml")
        
        # 获取主项目根目录 - 使用相对路径 - _data/plugins
        self.root_dir = os.path.dirname(self.plugin_dir)  # 指向_data/plugins目录
        logger.critical(f"[DependencyManager] 根目录设置为: {self.root_dir}")
            
        # 插件目录就是根目录本身
        self.plugins_dir = self.root_dir
        logger.critical(f"[DependencyManager] 插件目录设置为: {self.plugins_dir}")

        # 插件市场配置（默认值，可被 config.toml 覆盖）
        self.market_query_cmd = "插件查询"
        self.market_submit_cmd = "插件提交"
        self.market_page_size = 5
        self.market_cache_path = os.path.join(self.plugin_dir, "plugin_market_cache.json")
        self.market_cache_timeout_seconds = 3600
        
        # 加载配置
        self.load_config()
        
        # 加载主配置中的 GitHub 反代设置
        self.load_github_proxy()
        
        logger.critical(f"[DependencyManager] 插件初始化完成, 启用状态: {self.enable}, 优先级: 80")
        
    def load_config(self):
        """加载配置文件"""
        try:
            logger.critical(f"[DependencyManager] 尝试从 {self.config_path} 加载配置")
            
            with open(self.config_path, "rb") as f:
                config = tomllib.load(f)
                
            # 读取基本配置
            basic_config = config.get("basic", {})
            self.enable = basic_config.get("enable", False)
            self.admin_list = basic_config.get("admin_list", [])
            self.allowed_packages = basic_config.get("allowed_packages", [])
            self.check_allowed = basic_config.get("check_allowed", False)
            
            # 读取命令配置
            cmd_config = config.get("commands", {})
            self.install_cmd = cmd_config.get("install", "!pip install")
            self.show_cmd = cmd_config.get("show", "!pip show")
            self.list_cmd = cmd_config.get("list", "!pip list")
            self.uninstall_cmd = cmd_config.get("uninstall", "!pip uninstall")
            
            # 读取插件安装配置 - 使用唤醒词
            self.github_install_prefix = cmd_config.get("github_install", "github")
            self.market_query_cmd = cmd_config.get("market_query", "插件查询")
            self.market_submit_cmd = cmd_config.get("market_submit", "插件提交")
            
            logger.critical(f"[DependencyManager] 配置加载成功")
            logger.critical(f"[DependencyManager] 启用状态: {self.enable}")
            logger.critical(f"[DependencyManager] 管理员列表: {self.admin_list}")
            logger.critical(f"[DependencyManager] GitHub前缀: '{self.github_install_prefix}'")
            
        except Exception as e:
            logger.error(f"[DependencyManager] 加载配置失败: {str(e)}")
            self.enable = False
            self.admin_list = []
            self.allowed_packages = []
            self.check_allowed = False
            self.install_cmd = "!pip install"
            self.show_cmd = "!pip show"
            self.list_cmd = "!pip list"
            self.uninstall_cmd = "!pip uninstall"
            self.github_install_prefix = "github"
            self.market_query_cmd = "插件查询"
            self.market_submit_cmd = "插件提交"

    def _market_source_id(self, base_url: str) -> str:
        base = (base_url or "").strip()
        if not base:
            return "unknown"
        if base.startswith("http://") or base.startswith("https://"):
            parsed = urlparse(base)
            if parsed.netloc:
                return parsed.netloc
        return base.replace("http://", "").replace("https://", "").strip("/")

    def _split_list(self, raw_value: str) -> list:
        if not raw_value:
            return []
        items = re.split(r"[,\n，;；]+", raw_value)
        return [item.strip() for item in items if item and item.strip()]

    def _normalize_market_github_url(self, github_url: str) -> str:
        url = (github_url or "").strip()
        if not url:
            return ""
        if re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", url):
            return f"https://github.com/{url}"
        if url.startswith("github.com"):
            return f"https://{url}"
        if "github.com" in url and not url.startswith("http"):
            return f"https://{url}"
        return url

    def _build_market_submit_help(self) -> str:
        return (
            "插件提交格式：\n"
            "插件提交 名称|简介|作者|版本|GitHub地址|标签(可选)|依赖(可选)\n"
            "示例：\n"
            "插件提交 依赖管理器|用于安装插件|老夏|1.0.0|https://github.com/xxx/yyy|工具,运维|requests>=2.32\n"
        )

    def _parse_market_submit(self, args: str, sender_id: str) -> tuple:
        parts = [item.strip() for item in (args or "").split("|")]
        while parts and parts[-1] == "":
            parts.pop()
        if len(parts) < 5:
            return None, "参数不足，请至少填写名称、简介、作者、版本、GitHub地址"

        name, description, author, version, github_url = parts[:5]
        if not all([name, description, author, version, github_url]):
            return None, "必填字段不能为空"

        tags = self._split_list(parts[5]) if len(parts) > 5 else []
        requirements = self._split_list(parts[6]) if len(parts) > 6 else []
        normalized_github = self._normalize_market_github_url(github_url)
        if not normalized_github:
            return None, "GitHub 地址无效"

        plugin_data = {
            "name": name,
            "description": description,
            "author": author,
            "version": version,
            "github_url": normalized_github,
            "tags": tags,
            "requirements": requirements,
            "submitted_by": sender_id,
            "submitted_at": datetime.now().isoformat(),
            "status": "pending",
        }
        return plugin_data, None

    def _truncate_text(self, text: str, limit: int = 200) -> str:
        if text is None:
            return ""
        text = str(text).strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _submit_market_plugin(self, plugin_data: dict) -> dict:
        base_urls = self._plugin_market_base_urls()
        if not base_urls:
            return {"success": False, "error": "未配置插件市场地址"}

        headers = {
            "User-Agent": "AllBot/DependencyManager",
            "Content-Type": "application/json",
        }
        results = {}
        success_count = 0
        for base_url in base_urls:
            source_id = self._market_source_id(base_url)
            url = self._build_market_url(base_url, "/plugins/")
            try:
                response = requests.post(url, json=plugin_data, headers=headers, timeout=15)
                if response.status_code == 200:
                    results[source_id] = {"success": True, "base_url": base_url}
                    success_count += 1
                else:
                    error_text = self._truncate_text(response.text)
                    results[source_id] = {
                        "success": False,
                        "base_url": base_url,
                        "error": f"{response.status_code} - {error_text}",
                    }
            except Exception as e:
                results[source_id] = {
                    "success": False,
                    "base_url": base_url,
                    "error": self._truncate_text(str(e)),
                }

        if success_count > 0:
            return {
                "success": True,
                "partial": success_count != len(base_urls),
                "results": results,
            }
        return {"success": False, "error": "插件市场提交失败", "results": results}

    def _format_market_submit_result(self, result: dict) -> str:
        if not result.get("success"):
            lines = [f"❌ 插件提交失败：{result.get('error', '未知错误')}"]
        else:
            if result.get("partial"):
                lines = ["✅ 插件已提交（部分市场失败）"]
            else:
                lines = ["✅ 插件已提交到插件市场"]

        results = result.get("results") or {}
        for source_id, detail in results.items():
            if detail.get("success"):
                lines.append(f"{source_id}：成功")
            else:
                error_text = detail.get("error", "未知错误")
                lines.append(f"{source_id}：失败（{error_text}）")

        return "\n".join(lines)
    
    def load_github_proxy(self):
        """加载主配置中的 GitHub 反代设置"""
        try:
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            
            # 读取 github-proxy 配置
            self.github_proxy = main_config.get("AllBot", {}).get("github-proxy", "")
            
            logger.critical(f"[DependencyManager] GitHub反代地址: '{self.github_proxy}'")
            
            if self.github_proxy and not self.github_proxy.endswith("/"):
                # 确保反代地址以 / 结尾
                self.github_proxy = self.github_proxy + "/"
                logger.warning(f"[DependencyManager] GitHub反代地址已自动添加结尾斜杠: '{self.github_proxy}'")
                
        except Exception as e:
            logger.error(f"[DependencyManager] 加载主配置失败: {str(e)}")
            self.github_proxy = ""

    def _plugin_market_base_urls(self) -> list:
        env_value = os.environ.get("PLUGIN_MARKET_BASE_URLS", "").strip()
        base_urls = []
        if env_value:
            for item in env_value.split(","):
                url = item.strip()
                if url:
                    base_urls.append(url)
        else:
            single_url = os.environ.get("PLUGIN_MARKET_BASE_URL", "").strip()
            if single_url:
                base_urls.append(single_url)
            else:
                base_urls = [
                    "http://v.sxkiss.top",
                    "http://xianan.xin:1562/api",
                ]

        deduped = []
        seen = set()
        for url in base_urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    def _build_market_url(self, base_url: str, path: str) -> str:
        base = (base_url or "").rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _normalize_market_plugin(self, raw_plugin: dict) -> dict:
        if not isinstance(raw_plugin, dict):
            return {}
        return {
            "name": raw_plugin.get("name") or "Unknown Plugin",
            "description": raw_plugin.get("description") or "",
            "author": raw_plugin.get("author") or "未知作者",
            "github_url": raw_plugin.get("github_url") or raw_plugin.get("github") or "",
        }

    def _load_market_cache(self) -> dict:
        if not os.path.exists(self.market_cache_path):
            return {}
        try:
            with open(self.market_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"[DependencyManager] 读取插件市场缓存失败: {e}")
            return {}

    def _write_market_cache(self, plugins: list):
        payload = {
            "plugins": plugins,
            "cached_at": datetime.now().isoformat(),
        }
        try:
            with open(self.market_cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DependencyManager] 写入插件市场缓存失败: {e}")

    def _is_cache_fresh(self, cached_at: str) -> bool:
        if not cached_at:
            return False
        try:
            cached_time = datetime.fromisoformat(cached_at)
        except Exception:
            return False
        return (datetime.now() - cached_time).total_seconds() <= self.market_cache_timeout_seconds

    def _fetch_market_plugins(self) -> list:
        base_urls = self._plugin_market_base_urls()
        if not base_urls:
            return []

        all_plugins = []
        headers = {
            "User-Agent": "AllBot/DependencyManager",
        }
        for base_url in base_urls:
            url = self._build_market_url(base_url, "/plugins/?status=approved")
            try:
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code != 200:
                    logger.warning(f"[DependencyManager] 获取插件市场失败: {response.status_code} - {url}")
                    continue
                data = response.json()
            except Exception as e:
                logger.warning(f"[DependencyManager] 获取插件市场异常: {e}")
                continue

            if isinstance(data, dict):
                plugins = data.get("plugins", [])
            elif isinstance(data, list):
                plugins = data
            else:
                plugins = []

            for plugin in plugins:
                normalized = self._normalize_market_plugin(plugin)
                if normalized:
                    all_plugins.append(normalized)

        return all_plugins

    async def _sync_market_cache(self):
        plugins = await asyncio.to_thread(self._fetch_market_plugins)
        if plugins:
            self._write_market_cache(plugins)
            logger.info(f"[DependencyManager] 插件市场缓存更新成功，共 {len(plugins)} 个插件")
        else:
            logger.warning("[DependencyManager] 插件市场缓存更新失败或为空")

    @schedule("interval", minutes=30)
    async def _scheduled_market_cache(self, bot=None):
        if not self.enable:
            return
        await self._sync_market_cache()

    async def async_init(self):
        if not self.enable:
            return
        await self._sync_market_cache()
        return

    def _build_install_help_text(self) -> str:
        return (
            "安装方法：\n"
            f"1. `{self.github_install_prefix} 用户名/仓库名`\n"
            f"2. `{self.github_install_prefix} https://github.com/用户名/仓库名.git`\n"
            "提示：安装后需要重启机器人加载插件。\n"
        )

    def _format_market_page(self, plugins: list, page: int) -> str:
        if page < 1:
            page = 1
        total = len(plugins)
        page_size = self.market_page_size
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        items = plugins[start:end]

        lines = [
            self._build_install_help_text().strip(),
            f"插件查询 第{page}页/共{total_pages}页（每页{page_size}个）",
        ]

        if not items:
            lines.append("暂无插件数据。")
            return "\n".join(lines)

        index = start + 1
        for plugin in items:
            name = plugin.get("name") or "Unknown Plugin"
            description = plugin.get("description") or ""
            author = plugin.get("author") or "未知作者"
            github_url = plugin.get("github_url") or ""
            lines.append(
                f"{index}. 插件名：{name} | 插件介绍：{description} | 作者：{author} | 地址：{github_url}"
            )
            index += 1

        return "\n".join(lines)
    
    def _build_github_url(self, github_url: str) -> str:
        """
        构建带反代的 GitHub URL

        Args:
            github_url: 原始 GitHub URL (例如: https://github.com/user/repo)

        Returns:
            带反代的 URL 或原始 URL
        """
        url = (github_url or "").strip()
        if not url:
            return url

        proxy = (getattr(self, "github_proxy", "") or "").strip()
        if not proxy:
            return url

        # 统一补全协议，避免拼出 "/https://..." 这种非法 URL
        if proxy.startswith("//"):
            proxy = "https:" + proxy
        elif not re.match(r"^https?://", proxy, flags=re.I):
            proxy = "https://" + proxy.lstrip("/")

        if not proxy.endswith("/"):
            proxy += "/"

        # 已带反代前缀则不重复拼接
        if url.startswith(proxy) or proxy.rstrip("/") in url:
            return url

        # 只代理 github.com 链接
        if "github.com" not in url:
            return url

        proxied_url = f"{proxy}{url}"
        logger.debug(f"[DependencyManager] 构建反代URL: {url} -> {proxied_url}")
        return proxied_url
    
    @on_text_message(priority=80)
    async def handle_text_message(self, bot: WechatAPIClient, message: dict):
        """处理文本消息，检查是否为依赖管理命令"""
        if not self.enable:
            logger.debug("[DependencyManager] 插件未启用，跳过处理")
            return True  # 插件未启用，允许其他插件处理
            
        # 获取消息内容和发送者 - 修改为使用正确的键名
        content = message.get("Content", "").strip()
        from_user = message.get("SenderWxid", "")
        conversation_id = message.get("FromWxid", "")
        
        # 检查是否为管理员
        sender_id = from_user
        if not sender_id and "IsGroup" in message and message["IsGroup"]:
            # 如果是群聊消息，则SenderWxid应该已经包含发送者ID
            logger.debug(f"[DependencyManager] 群消息，发送者ID: {sender_id}")
            
        # 检查是否为管理员
        if sender_id not in self.admin_list:
            logger.debug("[DependencyManager] 非管理员消息，sender={} conversation={}", sender_id, conversation_id)
            return True  # 非管理员，允许其他插件处理
        
        logger.info("[DependencyManager] 管理员命令进入处理，sender={} conversation={}", sender_id, conversation_id)
        
        # ====================== 命令处理部分 ======================
        # 按照优先级排序，先处理特殊命令，再处理标准命令模式
        
        # 1. 测试命令 - 用于诊断插件是否正常工作
        if content == "!test dm":
            await bot.send_text_message(conversation_id, "✅ DependencyManager插件工作正常！")
            logger.info("[DependencyManager] 测试命令响应成功")
            return False
        
        # 2. GitHub相关命令处理 - 优先级最高
        
        # 2.1 检查是否明确以GitHub前缀开头 - 要求明确的安装意图
        starts_with_prefix = content.lower().startswith(self.github_install_prefix.lower())
        logger.debug("[DependencyManager] github_prefix_match={} sender={}", starts_with_prefix, sender_id)
        
        # 2.2 GitHub快捷命令 - GeminiImage特殊处理
        if starts_with_prefix and (content.strip().lower() == f"{self.github_install_prefix} gemini" or 
                                  content.strip().lower() == f"{self.github_install_prefix} geminiimage"):
            logger.info("[DependencyManager] 检测到GeminiImage快捷安装命令")
            await bot.send_text_message(conversation_id, "🔄 正在安装GeminiImage插件...")
            await self._handle_github_install(bot, conversation_id, "https://github.moeyy.xyz/https://github.com/NanSsye/GeminiImage.git")
            logger.info("[DependencyManager] GeminiImage快捷安装完成，阻止后续插件处理")
            return False

        # 2.5 插件市场提交命令（插件提交）
        if content.startswith(self.market_submit_cmd):
            args = content.replace(self.market_submit_cmd, "", 1).strip()
            if not args or args.lower() in {"help", "帮助", "?", "说明"}:
                await bot.send_text_message(conversation_id, self._build_market_submit_help())
                return False

            plugin_data, error = self._parse_market_submit(args, sender_id)
            if error:
                await bot.send_text_message(conversation_id, f"⚠️ {error}\n\n{self._build_market_submit_help()}")
                return False

            await bot.send_text_message(conversation_id, "🔄 正在提交插件到市场，请稍候...")
            result = await asyncio.to_thread(self._submit_market_plugin, plugin_data)
            await bot.send_text_message(conversation_id, self._format_market_submit_result(result))
            logger.info("[DependencyManager] 插件提交命令处理完成")
            return False

        # 2.6 插件市场查询命令（插件查询）
        if content.startswith(self.market_query_cmd):
            args = content.replace(self.market_query_cmd, "", 1).strip()
            page = 1
            if args:
                try:
                    page = int(args.split()[0])
                except Exception:
                    page = 1

            cache = self._load_market_cache()
            plugins = cache.get("plugins", []) if isinstance(cache, dict) else []
            cached_at = cache.get("cached_at") if isinstance(cache, dict) else ""

            if not plugins or not self._is_cache_fresh(cached_at):
                await self._sync_market_cache()
                cache = self._load_market_cache()
                plugins = cache.get("plugins", []) if isinstance(cache, dict) else []

            message = self._format_market_page(plugins, page)
            await bot.send_text_message(conversation_id, message)
            logger.info("[DependencyManager] 插件查询命令处理完成")
            return False
            
        # 2.3 GitHub帮助命令
        if content.strip().lower() == f"{self.github_install_prefix} help":
            help_text = f"""📦 GitHub插件安装帮助:

1. 安装GitHub上的插件:
   {self.github_install_prefix} https://github.com/用户名/插件名.git

2. 例如，安装GeminiImage插件:
   {self.github_install_prefix} https://github.com/NanSsye/GeminiImage.git
   
3. 简化格式:
   {self.github_install_prefix} 用户名/插件名
   
4. 快捷命令安装GeminiImage:
   {self.github_install_prefix} gemini

5. 插件会自动被克隆到插件目录并安装依赖

注意: 安装后需要重启机器人以加载新插件。
"""
            await bot.send_text_message(conversation_id, help_text)
            logger.info("[DependencyManager] GitHub安装帮助命令响应成功")
            return False
            
        # 2.4 标准GitHub安装命令处理 - 必须以明确的前缀开头
        if starts_with_prefix:
            logger.info("[DependencyManager] 检测到 GitHub 安装命令，sender={}", sender_id)
            # 获取前缀后面的内容
            command_content = content[len(self.github_install_prefix):].strip()
            logger.debug("[DependencyManager] GitHub 命令内容: {}", command_content)
            
            # 处理快捷命令 - gemini
            if command_content.lower() == "gemini" or command_content.lower() == "geminiimage":
                logger.info("[DependencyManager] 检测到GeminiImage快捷安装命令")
                await self._handle_github_install(bot, conversation_id, "https://github.moeyy.xyz/https://github.com/NanSsye/GeminiImage.git")
                logger.info("[DependencyManager] GeminiImage安装命令处理完成，返回False阻止后续处理")
                return False
                
            # 处理标准GitHub URL
            elif command_content.startswith("https://github.com") or command_content.startswith("github.com"):
                logger.info(f"[DependencyManager] 检测到GitHub URL: {command_content}")
                await self._handle_github_install(bot, conversation_id, command_content)
                logger.info("[DependencyManager] GitHub URL安装命令处理完成，返回False阻止后续处理")
                return False
                
            # 处理简化格式 - 用户名/仓库名
            elif "/" in command_content and not command_content.startswith("!"):
                # 检查是否符合 用户名/仓库名 格式
                if re.match(r'^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$', command_content.strip()):
                    repo_path = command_content.strip()
                    logger.info(f"[DependencyManager] 检测到简化的GitHub路径: {repo_path}")
                    github_url = f"https://github.com/{repo_path}"
                    logger.info(f"[DependencyManager] 构建GitHub URL: {github_url}")
                    await self._handle_github_install(bot, conversation_id, github_url)
                    logger.info("[DependencyManager] 简化GitHub路径安装命令处理完成，返回False阻止后续处理")
                    return False
            
            # 格式不正确
            else:
                await bot.send_text_message(conversation_id, f"⚠️ GitHub安装命令格式不正确。正确格式为: \n1. {self.github_install_prefix} https://github.com/用户名/插件名.git\n2. {self.github_install_prefix} 用户名/插件名")
                logger.info("[DependencyManager] GitHub格式不正确，已发送提示，返回False阻止后续处理")
                return False
            
            # 如果是以GitHub前缀开头但没有匹配到任何处理分支，也阻止后续处理
            logger.info("[DependencyManager] 命令以github开头但未匹配任何处理逻辑，默认阻止后续处理")
            return False
        
        # 忽略智能识别GitHub URL的逻辑，必须以明确的前缀开始才处理
        
        # 3. 依赖管理命令
        
        # 3.1 处理安装命令
        if content.startswith(self.install_cmd):
            await self._handle_install(bot, conversation_id, content.replace(self.install_cmd, "").strip())
            logger.debug(f"[DependencyManager] 处理安装命令完成，阻止后续插件")
            return False  # 命令已处理，不传递给其他插件
            
        # 3.2 处理查询命令
        elif content.startswith(self.show_cmd):
            await self._handle_show(bot, conversation_id, content.replace(self.show_cmd, "").strip())
            logger.debug(f"[DependencyManager] 处理查询命令完成，阻止后续插件")
            return False
            
        # 3.3 处理列表命令
        elif content.startswith(self.list_cmd):
            await self._handle_list(bot, conversation_id)
            logger.debug(f"[DependencyManager] 处理列表命令完成，阻止后续插件")
            return False
            
        # 3.4 处理卸载命令
        elif content.startswith(self.uninstall_cmd):
            await self._handle_uninstall(bot, conversation_id, content.replace(self.uninstall_cmd, "").strip())
            logger.debug(f"[DependencyManager] 处理卸载命令完成，阻止后续插件")
            return False
            
        # 3.5 处理帮助命令
        elif content.strip() == "!pip help" or content.strip() == "!pip":
            await self._send_help(bot, conversation_id)
            logger.debug(f"[DependencyManager] 处理帮助命令完成，阻止后续插件")
            return False
            
        # 3.6 处理导入检查命令
        elif content.startswith("!import"):
            package = content.replace("!import", "").strip()
            await self._check_import(bot, conversation_id, package)
            logger.debug(f"[DependencyManager] 处理导入检查命令完成，阻止后续插件")
            return False
            
        # 不是本插件的命令
        logger.debug(f"[DependencyManager] 非依赖管理相关命令，允许其他插件处理")
        return True  # 不是命令，允许其他插件处理
    
    async def _handle_install(self, bot: WechatAPIClient, chat_id: str, package_spec: str):
        """处理安装依赖包命令"""
        if not package_spec:
            await bot.send_text_message(chat_id, "请指定要安装的包，例如: !pip install packagename==1.0.0")
            return
            
        # 检查是否在允许安装的包列表中
        base_package = package_spec.split("==")[0].split(">=")[0].split(">")[0].split("<")[0].strip()
        if self.check_allowed and self.allowed_packages and base_package not in self.allowed_packages:
            await bot.send_text_message(chat_id, f"⚠️ 安全限制: {base_package} 不在允许安装的包列表中")
            return
            
        await bot.send_text_message(chat_id, f"📦 正在安装: {package_spec}...")
        
        try:
            # 执行pip安装命令
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", package_spec],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # 安装成功
                output = f"✅ 安装成功: {package_spec}\n\n{stdout}"
                # 如果输出太长，只取前后部分
                if len(output) > 1000:
                    output = output[:500] + "\n...\n" + output[-500:]
                await bot.send_text_message(chat_id, output)
            else:
                # 安装失败
                error = f"❌ 安装失败: {package_spec}\n\n{stderr}"
                # 如果输出太长，只取前后部分
                if len(error) > 1000:
                    error = error[:500] + "\n...\n" + error[-500:]
                await bot.send_text_message(chat_id, error)
                
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 执行安装命令时出错: {str(e)}")
    
    async def _handle_github_install(self, bot: WechatAPIClient, chat_id: str, github_url: str):
        """处理从Github安装插件的命令"""
        logger.critical(f"[DependencyManager] 开始处理GitHub插件安装，URL: {github_url}")
        
        # 处理各种GitHub URL格式
        if not github_url:
            logger.warning("[DependencyManager] GitHub URL为空")
            await bot.send_text_message(chat_id, "请提供有效的GitHub仓库URL，例如: github https://github.com/用户名/插件名.git")
            return
            
        # 标准化GitHub URL
        # 处理不包含https://的情况
        if not github_url.startswith("http"):
            if github_url.startswith("github.com"):
                github_url = "https://" + github_url
            elif "github.com" in github_url:
                # 尝试提取用户名/仓库名
                match = re.search(r'(?:github\.com[:/])?([^/\s]+/[^/\s]+)(?:\.git)?', github_url)
                if match:
                    repo_path = match.group(1)
                    github_url = f"https://github.com/{repo_path}"
                else:
                    github_url = "https://github.com/" + github_url.strip()
        
        logger.critical(f"[DependencyManager] 标准化后的URL: {github_url}")
        
        # 验证URL格式
        if not github_url.startswith("https://github.com"):
            logger.warning(f"[DependencyManager] 无效的GitHub URL: {github_url}")
            await bot.send_text_message(chat_id, "请提供有效的GitHub仓库URL，例如: github https://github.com/用户名/插件名.git")
            return
        
        # 确保URL以.git结尾
        if github_url.endswith(".git"):
            github_url = github_url[:-4]  # 移除.git后缀，为了构建zip下载链接
        
        # 从URL提取插件名称和仓库信息
        repo_match = re.search(r'https://github\.com/([^/]+)/([^/]+)$', github_url)
        if not repo_match:
            logger.warning(f"[DependencyManager] 无法从URL中提取仓库信息: {github_url}")
            await bot.send_text_message(chat_id, f"⚠️ 无法从URL中提取仓库信息: {github_url}")
            return
        
        user_name = repo_match.group(1)
        repo_name = repo_match.group(2).rstrip("/")
        # 仓库名可能含连字符，不能直接当 plugins 子目录名（importlib 要求合法标识符）
        provisional_name = self._sanitize_plugin_package_name(repo_name)
        existing_dir = self._find_existing_plugin_dir(repo_name, provisional_name)
        plugin_name = os.path.basename(existing_dir) if existing_dir else provisional_name
        plugin_target_dir = existing_dir or os.path.join(self.plugins_dir, plugin_name)

        logger.critical(f"[DependencyManager] 提取到用户名: {user_name}, 仓库名: {repo_name}")
        logger.critical(
            f"[DependencyManager] 目标目录: {plugin_target_dir} (repo={repo_name} → package={plugin_name})"
        )
        if plugin_name != repo_name:
            await bot.send_text_message(
                chat_id,
                f"📦 仓库名 `{repo_name}` 含非法包字符，将安装为合法目录 `{plugin_name}`",
            )

        # 检查插件目录是否已存在（含历史连字符目录）
        if os.path.exists(plugin_target_dir):
            logger.info(f"[DependencyManager] 插件目录已存在，尝试更新: {plugin_target_dir}")
            await bot.send_text_message(chat_id, f"⚠️ 插件 {plugin_name} 目录已存在，尝试更新...")
            try:
                git_installed = self._check_git_installed()
                if git_installed and os.path.isdir(os.path.join(plugin_target_dir, ".git")):
                    process = subprocess.Popen(
                        ["git", "pull", "origin", "main"],
                        cwd=plugin_target_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    stdout, stderr = process.communicate()
                    logger.info(f"[DependencyManager] Git pull结果：退出码 {process.returncode}")
                    if process.returncode == 0:
                        await bot.send_text_message(chat_id, f"✅ 成功更新插件 {plugin_name}!\n\n{stdout}")
                    else:
                        # main 分支失败时再试 master
                        process = subprocess.Popen(
                            ["git", "pull", "origin", "master"],
                            cwd=plugin_target_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )
                        stdout, stderr = process.communicate()
                        if process.returncode == 0:
                            await bot.send_text_message(chat_id, f"✅ 成功更新插件 {plugin_name}!\n\n{stdout}")
                        else:
                            logger.error(f"[DependencyManager] 更新插件失败: {stderr}")
                            await bot.send_text_message(chat_id, f"❌ 更新插件失败: {stderr}")
                            return
                else:
                    await bot.send_text_message(chat_id, "⚠️ 未检测到可用 git 仓库，尝试通过下载 ZIP 方式更新...")
                    success = await self._download_github_zip(
                        bot, chat_id, user_name, repo_name, plugin_target_dir, is_update=True
                    )
                    if not success:
                        return

                # 更新后可能需要把旧连字符目录迁移到合法包名
                plugin_target_dir, plugin_name = self._finalize_plugin_install_dir(
                    plugin_target_dir, repo_name
                )
                self._ensure_plugin_config(plugin_target_dir)
                await self._install_plugin_requirements(bot, chat_id, plugin_target_dir)
                await self._try_load_installed_plugin(bot, chat_id, plugin_target_dir, plugin_name)
            except Exception as e:
                logger.exception(f"[DependencyManager] 更新插件时出错")
                await bot.send_text_message(chat_id, f"❌ 更新插件时出错: {str(e)}")
            return

        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                logger.info(f"[DependencyManager] 创建临时目录: {temp_dir}")
                await bot.send_text_message(chat_id, f"🔄 正在从GitHub下载插件 {repo_name}...")

                git_installed = self._check_git_installed()
                logger.info(f"[DependencyManager] Git命令安装状态: {git_installed}")

                if git_installed:
                    clone_url = self._build_github_url(f"{github_url}.git")
                    logger.info(f"[DependencyManager] 使用git克隆: {clone_url} 到 {temp_dir}")
                    process = subprocess.Popen(
                        ["git", "clone", "--depth", "1", clone_url, temp_dir],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    stdout, stderr = process.communicate()
                    logger.info(f"[DependencyManager] Git clone结果：退出码 {process.returncode}")
                    if process.returncode != 0:
                        logger.error(f"[DependencyManager] Git克隆失败，尝试使用ZIP方式下载: {stderr}")
                        success = await self._download_github_zip(bot, chat_id, user_name, repo_name, temp_dir)
                        if not success:
                            return
                else:
                    logger.info(f"[DependencyManager] Git未安装，使用ZIP方式下载")
                    success = await self._download_github_zip(bot, chat_id, user_name, repo_name, temp_dir)
                    if not success:
                        return

                # 根据源码中的插件类名决定最终目录名（优先于仓库名）
                plugin_name = self._resolve_plugin_package_name(repo_name, temp_dir)
                plugin_target_dir = os.path.join(self.plugins_dir, plugin_name)
                if plugin_name != repo_name:
                    logger.info(
                        f"[DependencyManager] 目录规范化: repo={repo_name} → package={plugin_name}"
                    )
                    await bot.send_text_message(
                        chat_id,
                        f"📦 安装目录规范化为 `{plugin_name}`（兼容 AllBot 插件加载）",
                    )

                # 若规范化后目标已存在，改为更新覆盖（保留 config.toml）
                if os.path.exists(plugin_target_dir):
                    await bot.send_text_message(
                        chat_id, f"⚠️ 目标目录 `{plugin_name}` 已存在，将覆盖代码并保留 config.toml"
                    )
                    self._copy_plugin_tree(temp_dir, plugin_target_dir, preserve_config=True)
                else:
                    logger.info(f"[DependencyManager] 创建插件目录: {plugin_target_dir}")
                    os.makedirs(plugin_target_dir, exist_ok=True)
                    self._copy_plugin_tree(temp_dir, plugin_target_dir, preserve_config=False)

                self._ensure_plugin_config(plugin_target_dir)
                logger.info(f"[DependencyManager] 文件复制完成")
                await bot.send_text_message(chat_id, f"✅ 成功下载插件 {plugin_name}!")

                await self._install_plugin_requirements(bot, chat_id, plugin_target_dir)
                await self._try_load_installed_plugin(bot, chat_id, plugin_target_dir, plugin_name)
            except Exception as e:
                logger.exception(f"[DependencyManager] 安装插件时出错")
                await bot.send_text_message(chat_id, f"❌ 安装插件时出错: {str(e)}")
    
    def _check_git_installed(self):
        """检查git命令是否可用"""
        try:
            process = subprocess.Popen(
                ["git", "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            process.communicate()
            return process.returncode == 0
        except Exception:
            return False

    # ── Plugin package name helpers ─────────────────────────
    # AllBot 通过 importlib.import_module(f"plugins.{dirname}.main") 加载插件，
    # 目录名必须是合法 Python 标识符。GitHub 仓库名常含连字符（如 allbot-xxx），
    # 直接作为目录名会导致加载失败。

    _PLUGIN_DIR_PREFIXES = (
        "allbot-", "allbot_", "allbot-", "allbot_",
        "plugin-", "plugin_", "plugins-", "plugins_",
    )

    @staticmethod
    def _is_valid_plugin_package_name(name: str) -> bool:
        """目录名是否可作为 plugins.<name>.main 导入。"""
        if not name or not isinstance(name, str):
            return False
        if name in {"__pycache__", "tests", "test", "docs", "assets"}:
            return False
        if not name.isidentifier():
            return False
        if name.startswith("_"):
            return False
        return True

    @classmethod
    def _kebab_to_pascal(cls, name: str) -> str:
        """allbot-group-social-graph → GroupSocialGraph"""
        text = (name or "").strip()
        for prefix in cls._PLUGIN_DIR_PREFIXES:
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        parts = [p for p in re.split(r"[-_\s]+", text) if p]
        if not parts:
            return ""
        return "".join(p[:1].upper() + p[1:] for p in parts)

    @classmethod
    def _sanitize_plugin_package_name(cls, repo_name: str) -> str:
        """把 GitHub 仓库名规范成合法插件目录名。"""
        raw = (repo_name or "").strip().rstrip("/")
        if not raw:
            return "InstalledPlugin"
        if cls._is_valid_plugin_package_name(raw):
            return raw
        pascal = cls._kebab_to_pascal(raw)
        if cls._is_valid_plugin_package_name(pascal):
            return pascal
        # 最后兜底：仅保留字母数字下划线
        cleaned = re.sub(r"[^0-9A-Za-z_]", "_", raw)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        if cleaned and cleaned[0].isdigit():
            cleaned = f"Plugin_{cleaned}"
        if not cleaned:
            cleaned = "InstalledPlugin"
        if not cleaned.isidentifier():
            cleaned = "InstalledPlugin"
        return cleaned

    @staticmethod
    def _detect_plugin_class_name(plugin_dir: str) -> str:
        """从 main.py 中探测 PluginBase 子类名（不 import，避免依赖未装齐）。"""
        main_py = os.path.join(plugin_dir, "main.py")
        if not os.path.isfile(main_py):
            return ""
        try:
            source = Path(main_py).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        # class Foo(PluginBase) / class Foo(xxx.PluginBase)
        matches = re.findall(
            r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*((?:[A-Za-z0-9_.;\s]*?)PluginBase)\s*\)",
            source,
        )
        for class_name, _base in matches:
            if class_name not in {"PluginBase"}:
                return class_name
        # 宽松兜底：任意 class XxxPlugin
        loose = re.findall(r"class\s+([A-Za-z_][A-Za-z0-9_]*Plugin)\s*\(", source)
        for class_name in loose:
            if class_name != "PluginBase":
                return class_name
        return ""

    def _resolve_plugin_package_name(self, repo_name: str, source_dir: str = "") -> str:
        """综合仓库名 + 源码类名，决定最终安装目录名。"""
        preferred = self._sanitize_plugin_package_name(repo_name)
        class_name = ""
        if source_dir and os.path.isdir(source_dir):
            class_name = self._detect_plugin_class_name(source_dir)
        # 类名本身合法时优先用类名（与 load_plugin 的 plugin_name=类名一致）
        if class_name and self._is_valid_plugin_package_name(class_name):
            return class_name
        return preferred

    def _find_existing_plugin_dir(self, repo_name: str, preferred_name: str) -> str:
        """查找已安装目录：优先合法包名，再兼容历史连字符仓库名。"""
        candidates = []
        for name in (preferred_name, repo_name, self._kebab_to_pascal(repo_name)):
            if name and name not in candidates:
                candidates.append(name)
        for name in candidates:
            path = os.path.join(self.plugins_dir, name)
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "main.py")):
                return path
        return ""

    def _finalize_plugin_install_dir(self, current_dir: str, repo_name: str) -> tuple:
        """更新后若目录名非法，迁移到合法包名（保留 config.toml）。"""
        current_name = os.path.basename(current_dir.rstrip(os.sep))
        if self._is_valid_plugin_package_name(current_name):
            # 若类名与目录不同且类名更合适，不强制迁移，避免破坏用户路径
            return current_dir, current_name

        target_name = self._resolve_plugin_package_name(repo_name, current_dir)
        target_dir = os.path.join(self.plugins_dir, target_name)
        if os.path.abspath(current_dir) == os.path.abspath(target_dir):
            return current_dir, current_name

        logger.warning(
            f"[DependencyManager] 迁移非法插件目录: {current_name} → {target_name}"
        )
        if os.path.exists(target_dir):
            # 目标已存在：把当前代码合并过去，保留目标 config
            self._copy_plugin_tree(current_dir, target_dir, preserve_config=True)
            # 不删除旧目录，避免误删用户数据；仅提示
            return target_dir, target_name

        os.makedirs(target_dir, exist_ok=True)
        self._copy_plugin_tree(current_dir, target_dir, preserve_config=False)
        # 迁移成功后尝试删除旧非法目录（失败忽略）
        try:
            shutil.rmtree(current_dir)
        except OSError as exc:
            logger.warning(f"[DependencyManager] 删除旧目录失败 {current_dir}: {exc}")
        return target_dir, target_name

    def _copy_plugin_tree(self, src_dir: str, dst_dir: str, *, preserve_config: bool = False) -> None:
        """复制插件文件树；preserve_config 时保留目标 config.toml。"""
        preserved: bytes | None = None
        config_path = os.path.join(dst_dir, "config.toml")
        if preserve_config and os.path.isfile(config_path):
            with open(config_path, "rb") as f:
                preserved = f.read()

        os.makedirs(dst_dir, exist_ok=True)
        for item in os.listdir(src_dir):
            if item in {".git", "__pycache__", ".github"}:
                continue
            s = os.path.join(src_dir, item)
            d = os.path.join(dst_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

        if preserved is not None:
            with open(config_path, "wb") as f:
                f.write(preserved)

    def _ensure_plugin_config(self, plugin_dir: str) -> None:
        """若无 config.toml，从 config.example.toml 复制。"""
        config_path = os.path.join(plugin_dir, "config.toml")
        if os.path.isfile(config_path):
            return
        for name in ("config.example.toml", "config.sample.toml", "config.toml.example"):
            example = os.path.join(plugin_dir, name)
            if os.path.isfile(example):
                shutil.copy2(example, config_path)
                logger.info(f"[DependencyManager] 已从 {name} 生成 config.toml")
                return
        # 最小可用配置，避免插件 __init__ 直接炸
        Path(config_path).write_text(
            "# 自动生成的最小配置，请按插件 README 修改\n"
            "[basic]\n"
            "enable = true\n",
            encoding="utf-8",
        )
        logger.info(f"[DependencyManager] 已生成最小 config.toml: {config_path}")

    async def _try_load_installed_plugin(
        self, bot: WechatAPIClient, chat_id: str, plugin_dir: str, package_name: str
    ) -> None:
        """安装/更新后尝试热加载；失败时提示重启。"""
        if not self._is_valid_plugin_package_name(package_name):
            await bot.send_text_message(
                chat_id,
                f"⚠️ 插件目录 `{package_name}` 不是合法 Python 包名，无法自动加载，请手动改名后重启。",
            )
            return
        class_name = self._detect_plugin_class_name(plugin_dir) or package_name
        try:
            from utils.plugin_manager import plugin_manager
        except Exception as exc:
            logger.warning(f"[DependencyManager] 无法导入 plugin_manager: {exc}")
            await bot.send_text_message(chat_id, "🔄 插件文件已就绪，请重启机器人以加载新插件。")
            return

        try:
            # 优先按类名加载（与 plugin_manager API 一致）
            ok = await plugin_manager.load_plugin_from_directory(bot, class_name)
            if not ok and class_name != package_name:
                ok = await plugin_manager.load_plugin_from_directory(bot, package_name)
            if ok:
                await bot.send_text_message(
                    chat_id,
                    f"🚀 插件 `{class_name}` 已尝试热加载成功（目录: {package_name}）。",
                )
            else:
                await bot.send_text_message(
                    chat_id,
                    f"🔄 插件已安装到 `{package_name}`，热加载未成功，请重启机器人或在管理后台启用。",
                )
        except Exception as exc:
            logger.exception(f"[DependencyManager] 热加载插件失败: {exc}")
            await bot.send_text_message(
                chat_id,
                f"🔄 插件已安装到 `{package_name}`，热加载出错: {exc}。请重启机器人。",
            )

    async def _download_github_zip(self, bot, chat_id, user_name, repo_name, target_dir, is_update=False):
        """使用requests下载GitHub仓库的ZIP文件"""
        try:
            # 构建ZIP下载链接
            original_zip_url = f"https://github.com/{user_name}/{repo_name}/archive/refs/heads/main.zip"
            zip_url = self._build_github_url(original_zip_url)
            logger.critical(f"[DependencyManager] 开始下载ZIP: {zip_url}")
            
            # 发送下载状态
            await bot.send_text_message(chat_id, f"📥 正在从GitHub下载ZIP文件...")
            
            # 下载ZIP文件
            response = requests.get(zip_url, timeout=30)
            if response.status_code != 200:
                # 尝试使用master分支
                original_zip_url = f"https://github.com/{user_name}/{repo_name}/archive/refs/heads/master.zip"
                zip_url = self._build_github_url(original_zip_url)
                logger.critical(f"[DependencyManager] 尝试下载master分支: {zip_url}")
                response = requests.get(zip_url, timeout=30)
                
            if response.status_code != 200:
                logger.error(f"[DependencyManager] 下载ZIP失败，状态码: {response.status_code}")
                await bot.send_text_message(chat_id, f"❌ 下载ZIP文件失败，HTTP状态码: {response.status_code}")
                return False
                
            # 解压ZIP文件
            logger.critical(f"[DependencyManager] 下载完成，文件大小: {len(response.content)} 字节")
            logger.critical(f"[DependencyManager] 解压ZIP文件到: {target_dir}")
            
            z = zipfile.ZipFile(io.BytesIO(response.content))
            
            # 检查ZIP文件内容
            zip_contents = z.namelist()
            logger.critical(f"[DependencyManager] ZIP文件内容: {', '.join(zip_contents[:5])}...")
            
            if is_update:
                # 更新时先备份配置文件
                config_files = []
                if os.path.exists(os.path.join(target_dir, "config.toml")):
                    with open(os.path.join(target_dir, "config.toml"), "rb") as f:
                        config_files.append(("config.toml", f.read()))
                
                # 清空目录（保留.git目录）
                for item in os.listdir(target_dir):
                    if item == ".git":
                        continue
                    item_path = os.path.join(target_dir, item)
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
            
            # 解压文件
            extract_dir = tempfile.mkdtemp()
            z.extractall(extract_dir)
            
            # ZIP文件解压后通常会有一个包含所有文件的顶级目录
            extracted_dirs = os.listdir(extract_dir)
            if len(extracted_dirs) == 1:
                extract_subdir = os.path.join(extract_dir, extracted_dirs[0])
                
                # 将文件从解压的子目录复制到目标目录
                for item in os.listdir(extract_subdir):
                    s = os.path.join(extract_subdir, item)
                    d = os.path.join(target_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            else:
                # 直接解压到目标目录
                for item in os.listdir(extract_dir):
                    s = os.path.join(extract_dir, item)
                    d = os.path.join(target_dir, item)
                    if os.path.isdir(s):
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)
            
            # 清理临时目录
            shutil.rmtree(extract_dir)
            
            # 如果是更新，恢复配置文件
            if is_update and config_files:
                for filename, content in config_files:
                    with open(os.path.join(target_dir, filename), "wb") as f:
                        f.write(content)
                logger.info(f"[DependencyManager] 已恢复配置文件")
            
            await bot.send_text_message(chat_id, f"✅ ZIP文件下载并解压成功")
            return True
        except Exception as e:
            logger.exception(f"[DependencyManager] 下载ZIP文件时出错")
            await bot.send_text_message(chat_id, f"❌ 下载ZIP文件时出错: {str(e)}")
            return False
    
    async def _install_plugin_requirements(self, bot: WechatAPIClient, chat_id: str, plugin_dir: str):
        """安装插件的依赖项"""
        requirements_file = os.path.join(plugin_dir, "requirements.txt")
        
        if not os.path.exists(requirements_file):
            await bot.send_text_message(chat_id, "📌 未找到requirements.txt文件，跳过依赖安装")
            return
        
        try:
            await bot.send_text_message(chat_id, "📦 正在安装插件依赖...")
            
            # 读取requirements.txt内容
            with open(requirements_file, "r") as f:
                requirements = f.read()
                
            # 显示依赖列表
            await bot.send_text_message(chat_id, f"📋 依赖列表:\n{requirements}")
            
            # 安装依赖
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-r", requirements_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                output = f"✅ 依赖安装成功!\n\n{stdout}"
                # 如果输出太长，只取前后部分
                if len(output) > 1000:
                    output = output[:500] + "\n...\n" + output[-500:]
                await bot.send_text_message(chat_id, output)
                
                # 提示重启机器人
                await bot.send_text_message(chat_id, "🔄 插件安装完成！请重启机器人以加载新插件。")
            else:
                error = f"❌ 依赖安装失败:\n\n{stderr}"
                # 如果输出太长，只取前后部分
                if len(error) > 1000:
                    error = error[:500] + "\n...\n" + error[-500:]
                await bot.send_text_message(chat_id, error)
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 安装依赖时出错: {str(e)}")
    
    async def _handle_show(self, bot: WechatAPIClient, chat_id: str, package: str):
        """处理查询包信息命令"""
        if not package:
            await bot.send_text_message(chat_id, "请指定要查询的包，例如: !pip show packagename")
            return
            
        await bot.send_text_message(chat_id, f"🔍 正在查询: {package}...")
        
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "show", package],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # 查询成功
                await bot.send_text_message(chat_id, f"📋 {package} 信息:\n\n{stdout}")
            else:
                # 查询失败
                await bot.send_text_message(chat_id, f"❌ 查询失败: {package}\n\n{stderr}")
                
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 执行查询命令时出错: {str(e)}")
    
    async def _handle_list(self, bot: WechatAPIClient, chat_id: str):
        """处理列出所有包命令"""
        await bot.send_text_message(chat_id, "📋 正在获取已安装的包列表...")
        
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # 获取成功，但可能很长，分段发送
                if len(stdout) > 1000:
                    chunks = [stdout[i:i+1000] for i in range(0, len(stdout), 1000)]
                    await bot.send_text_message(chat_id, f"📦 已安装的包列表 (共{len(chunks)}段):")
                    for i, chunk in enumerate(chunks):
                        await bot.send_text_message(chat_id, f"📦 第{i+1}段:\n\n{chunk}")
                else:
                    await bot.send_text_message(chat_id, f"📦 已安装的包列表:\n\n{stdout}")
            else:
                # 获取失败
                await bot.send_text_message(chat_id, f"❌ 获取列表失败\n\n{stderr}")
                
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 执行列表命令时出错: {str(e)}")
    
    async def _handle_uninstall(self, bot: WechatAPIClient, chat_id: str, package: str):
        """处理卸载包命令"""
        if not package:
            await bot.send_text_message(chat_id, "请指定要卸载的包，例如: !pip uninstall packagename")
            return
            
        await bot.send_text_message(chat_id, f"🗑️ 正在卸载: {package}...")
        
        try:
            # 使用-y参数自动确认卸载
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "uninstall", "-y", package],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # 卸载成功
                await bot.send_text_message(chat_id, f"✅ 卸载成功: {package}\n\n{stdout}")
            else:
                # 卸载失败
                await bot.send_text_message(chat_id, f"❌ 卸载失败: {package}\n\n{stderr}")
                
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 执行卸载命令时出错: {str(e)}")
    
    async def _send_help(self, bot: WechatAPIClient, chat_id: str):
        """发送帮助信息"""
        help_text = f"""📚 依赖包管理插件使用帮助:

1️⃣ 安装包:
   {self.install_cmd} package_name
   {self.install_cmd} package_name==1.2.3  (指定版本)

2️⃣ 查询包信息:
   {self.show_cmd} package_name

3️⃣ 列出所有已安装的包:
   {self.list_cmd}

4️⃣ 卸载包:
   {self.uninstall_cmd} package_name

5️⃣ 检查包是否可以导入:
   !import package_name

6️⃣ 安装GitHub插件:
   {self.github_install_prefix} https://github.com/用户名/插件名.git

ℹ️ 仅允许管理员使用此功能
"""
        await bot.send_text_message(chat_id, help_text)
    
    async def _check_import(self, bot: WechatAPIClient, chat_id: str, package: str):
        """检查包是否可以成功导入"""
        if not package:
            await bot.send_text_message(chat_id, "请指定要检查的包，例如: !import packagename")
            return
            
        await bot.send_text_message(chat_id, f"🔍 正在检查是否可以导入: {package}...")
        
        try:
            # 尝试导入包
            importlib.import_module(package)
            await bot.send_text_message(chat_id, f"✅ {package} 可以成功导入!")
        except ImportError as e:
            await bot.send_text_message(chat_id, f"❌ 无法导入 {package}: {str(e)}")
        except Exception as e:
            await bot.send_text_message(chat_id, f"❌ 导入 {package} 时发生错误: {str(e)}")
            
    async def on_disable(self):
        """插件禁用时的清理工作"""
        await super().on_disable()
        logger.info("[DependencyManager] 插件已禁用") 
