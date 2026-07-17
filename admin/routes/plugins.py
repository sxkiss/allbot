"""
@input: fastapi, aiohttp, loguru, os, json, asyncio; require_auth from admin.utils; plugin_manager from utils
@output: FastAPI routes for plugin management and plugin market aggregation/proxy
@position: Admin routes layer handling plugin CRUD, market aggregation, submit, and caching
@auto-doc: Update header and folder INDEX.md when this file changes
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from fastapi import Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger


def register_plugins_routes(app, current_dir, plugin_manager=None):
    """
    注册插件管理相关路由

    Args:
        app: FastAPI 应用实例
        current_dir: 当前目录路径
        plugin_manager: 插件管理器实例
    """
    from fastapi import Depends
    from admin.utils import require_auth

    @app.get("/api/plugins", response_class=JSONResponse)
    async def api_plugins_list(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            try:
                from utils.plugin_manager import plugin_manager
            except ImportError as e:
                logger.error(f"导入plugin_manager失败: {str(e)}")
                return {"success": False, "error": f"导入plugin_manager失败: {str(e)}"}

            import os
            try:
                import tomllib as toml_parser
            except ImportError:
                try:
                    import tomli as toml_parser
                except ImportError as e:
                    logger.error(f"缺少TOML解析库: {str(e)}")
                    return {"success": False, "error": "缺少TOML解析库，请安装tomli或使用Python 3.11+"}

            # 获取插件信息列表
            plugins_info = plugin_manager.get_plugin_info()

            # 确保返回的数据是可序列化的
            if not isinstance(plugins_info, list):
                plugins_info = []
                logger.error("plugin_manager.get_plugin_info()返回了非列表类型")

            # 获取适配器信息
            adapters_info = []
            adapter_dir = "adapter"
            if os.path.exists(adapter_dir) and os.path.isdir(adapter_dir):
                for dirname in os.listdir(adapter_dir):
                    # 跳过特殊目录
                    if dirname.startswith('.') or dirname == '__pycache__':
                        continue
                    adapter_path = os.path.join(adapter_dir, dirname)
                    if os.path.isdir(adapter_path):
                        # 读取适配器的 config.toml
                        config_path = os.path.join(adapter_path, "config.toml")
                        adapter_info = {
                            "name": dirname,
                            "version": "未知",
                            "description": "适配器",
                            "author": "未知",
                            "enabled": False,
                            "type": "adapter"
                        }

                        if os.path.exists(config_path):
                            try:
                                with open(config_path, "rb") as f:
                                    config = toml_parser.load(f)
                                    adapter_info["version"] = config.get("version", "未知")
                                    adapter_info["description"] = config.get("description", "适配器")
                                    adapter_info["author"] = config.get("author", "未知")
                            except Exception as e:
                                logger.warning(f"读取适配器 {dirname} 配置失败: {str(e)}")

                        adapters_info.append(adapter_info)

            # 记录调试信息
            logger.debug(f"获取到{len(plugins_info)}个插件信息和{len(adapters_info)}个适配器信息")

            # 合并插件和适配器信息
            all_items = plugins_info + adapters_info

            return {
                "success": True,
                "data": {
                    "plugins": all_items
                }
            }
        except Exception as e:
            logger.error(f"获取插件信息失败: {str(e)}")
            return {"success": False, "error": f"获取插件信息失败: {str(e)}"}

    # API: 启用插件
    @app.post("/api/plugins/{plugin_name}/enable", response_class=JSONResponse)
    async def api_enable_plugin(plugin_name: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            from utils.plugin_manager import plugin_manager
            from admin.core.app_setup import get_bot_instance

            bot = get_bot_instance()
            if bot is None:
                return {"success": False, "error": "机器人实例未初始化，请确保机器人已启动"}

            success = await plugin_manager.load_plugin_from_directory(bot, plugin_name)
            return {"success": success}
        except Exception as e:
            logger.error(f"启用插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 禁用插件
    @app.post("/api/plugins/{plugin_name}/disable", response_class=JSONResponse)
    async def api_disable_plugin(plugin_name: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            from utils.plugin_manager import plugin_manager

            # 调用 unload_plugin 方法并设置 add_to_excluded 参数为 True
            # 这样会将插件添加到禁用列表中并保存到配置文件
            success = await plugin_manager.unload_plugin(plugin_name, add_to_excluded=True)
            return {"success": success}
        except Exception as e:
            logger.error(f"禁用插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除插件
    @app.post("/api/plugins/{plugin_name}/delete", response_class=JSONResponse)
    async def api_delete_plugin(plugin_name: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            from utils.plugin_manager import plugin_manager
            import shutil
            import os

            # 首先确保插件已经被卸载
            if plugin_name in plugin_manager.plugins:
                await plugin_manager.unload_plugin(plugin_name)

            # 查找插件目录
            plugin_dir = None
            for dirname in os.listdir("plugins"):
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                    try:
                        # 检查目录中的main.py是否包含该插件类
                        with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                            content = f.read()
                            if f"class {plugin_name}(" in content:
                                plugin_dir = f"plugins/{dirname}"
                                break
                    except Exception as e:
                        logger.error(f"检查插件目录时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "error": f"找不到插件 {plugin_name} 的目录"}

            # 防止删除核心插件
            if plugin_name == "ManagePlugin":
                return {"success": False, "error": "不能删除核心插件 ManagePlugin"}

            # 删除插件目录
            shutil.rmtree(plugin_dir)

            # 从插件信息中移除
            if plugin_name in plugin_manager.plugin_info:
                del plugin_manager.plugin_info[plugin_name]

            return {"success": True, "message": f"插件 {plugin_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除适配器
    @app.post("/api/adapters/{adapter_name}/delete", response_class=JSONResponse)
    async def api_delete_adapter(adapter_name: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            import shutil
            import os

            # 检查适配器目录是否存在
            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name} 的目录"}

            # 删除适配器目录
            shutil.rmtree(adapter_dir)
            logger.info(f"适配器 {adapter_name} 已成功删除")

            return {"success": True, "message": f"适配器 {adapter_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除适配器失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取适配器配置（可视化 schema + 原文）
    @app.get("/api/adapters/{adapter_name}/config", response_class=JSONResponse)
    async def api_get_adapter_config(adapter_name: str, request: Request, username: str = Depends(require_auth)):
        try:
            from admin.services.config_service import load_generic_config_view

            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name}"}

            config_path = Path(adapter_dir) / "config.toml"
            if not config_path.exists():
                return {"success": False, "error": f"适配器 {adapter_name} 没有配置文件"}

            view = load_generic_config_view(config_path, title=f"适配器 {adapter_name}")
            return {
                "success": True,
                "config": view["raw"],  # 兼容旧前端
                "data": view["values"],
                "schema": view["schema"],
                "raw": view["raw"],
                "path": view["path"],
                "values": view["values"],
            }
        except Exception as e:
            logger.error(f"获取适配器配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 保存适配器配置（可视化/原文）
    @app.post("/api/adapters/{adapter_name}/config", response_class=JSONResponse)
    async def api_save_adapter_config(adapter_name: str, request: Request, username: str = Depends(require_auth)):
        try:
            from admin.services.config_service import (
                load_generic_config_view,
                save_generic_config_raw,
                save_generic_config_values,
            )

            data = await request.json()
            adapter_dir = os.path.join("adapter", adapter_name)
            if not os.path.exists(adapter_dir) or not os.path.isdir(adapter_dir):
                return {"success": False, "error": f"找不到适配器 {adapter_name}"}

            config_path = Path(adapter_dir) / "config.toml"
            if not config_path.exists():
                return {"success": False, "error": f"适配器 {adapter_name} 没有配置文件"}

            # 兼容旧调用：{"config": "..."} 当作原文保存
            if isinstance(data, dict) and data.get("mode") == "raw":
                content = data.get("content")
                if not isinstance(content, str) or not content.strip():
                    return {"success": False, "error": "配置内容不能为空"}
                result = save_generic_config_raw(config_path, content)
            elif isinstance(data, dict) and "config" in data and data.get("mode") is None and "values" not in data:
                content = data.get("config")
                if not isinstance(content, str) or not content.strip():
                    return {"success": False, "error": "配置内容不能为空"}
                result = save_generic_config_raw(config_path, content)
            else:
                payload = data.get("values") if isinstance(data, dict) and "values" in data else data
                if not isinstance(payload, dict):
                    return {"success": False, "error": "可视化配置数据必须是对象"}
                result = save_generic_config_values(config_path, payload)

            view = load_generic_config_view(config_path, title=f"适配器 {adapter_name}")
            logger.info(f"适配器 {adapter_name} 配置文件已保存")
            return {
                "success": True,
                "message": "配置已保存，重启服务后生效",
                "backup": result.get("backup"),
                "path": result.get("path"),
                "data": view["values"],
                "schema": view["schema"],
                "raw": view["raw"],
                "config": view["raw"],
            }
        except Exception as e:
            logger.error(f"保存适配器配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 删除插件（备用路由）
    @app.post("/api/plugin/delete", response_class=JSONResponse)
    async def api_delete_plugin_alt(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            # 获取请求数据
            data = await request.json()
            plugin_name = data.get('plugin_id')

            if not plugin_name:
                return {"success": False, "error": "缺少插件ID参数"}

            from utils.plugin_manager import plugin_manager
            import shutil
            import os

            # 首先确保插件已经被卸载
            if plugin_name in plugin_manager.plugins:
                await plugin_manager.unload_plugin(plugin_name)

            # 查找插件目录
            plugin_dir = None
            for dirname in os.listdir("plugins"):
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                    try:
                        # 检查目录中的main.py是否包含该插件类
                        with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                            content = f.read()
                            if f"class {plugin_name}(" in content:
                                plugin_dir = f"plugins/{dirname}"
                                break
                    except Exception as e:
                        logger.error(f"检查插件目录时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "error": f"找不到插件 {plugin_name} 的目录"}

            # 防止删除核心插件
            if plugin_name == "ManagePlugin":
                return {"success": False, "error": "不能删除核心插件 ManagePlugin"}

            # 删除插件目录
            shutil.rmtree(plugin_dir)

            # 从插件信息中移除
            if plugin_name in plugin_manager.plugin_info:
                del plugin_manager.plugin_info[plugin_name]

            return {"success": True, "message": f"插件 {plugin_name} 已成功删除"}
        except Exception as e:
            logger.error(f"删除插件失败: {str(e)}")
            return {"success": False, "error": str(e)}



    # 辅助函数: 查找插件配置路径
    def find_plugin_config_path(plugin_id: str):
        """查找插件配置文件路径，尝试多个可能的位置"""
        # 首先尝试直接使用插件ID作为目录名
        possible_paths = [
            os.path.join("plugins", plugin_id, "config.toml"),  # 原始路径
            os.path.join("_data", "plugins", plugin_id, "config.toml"),  # _data目录下的路径
            os.path.join("..", "plugins", plugin_id, "config.toml"),  # 相对上级目录
            os.path.abspath(os.path.join("plugins", plugin_id, "config.toml")),  # 绝对路径
            os.path.join(os.path.dirname(os.path.dirname(current_dir)), "plugins", plugin_id, "config.toml")  # 项目根目录
        ]

        # 如果没有找到，尝试遍历所有插件目录查找匹配的插件类
        plugin_dirs = []
        for dirname in os.listdir("plugins"):
            if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                try:
                    # 检查目录中的main.py是否包含该插件类
                    with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                        content = f.read()
                        if f"class {plugin_id}(" in content:
                            plugin_dirs.append(dirname)
                except Exception as e:
                    logger.error(f"检查插件目录时出错: {str(e)}")

        # 将找到的目录添加到可能路径中
        for dirname in plugin_dirs:
            possible_paths.append(os.path.join("plugins", dirname, "config.toml"))

        # 检查环境变量定义的数据目录
        data_dir_env = os.environ.get('XYBOT_DATA_DIR')
        if data_dir_env:
            possible_paths.append(os.path.join(data_dir_env, "plugins", plugin_id, "config.toml"))

        # 检查Docker环境特定路径
        if os.path.exists('/.dockerenv') or os.path.exists('/run/.containerenv'):
            docker_paths = [
                os.path.join("/app/data/plugins", plugin_id, "config.toml"),
                os.path.join("/data/plugins", plugin_id, "config.toml"),
                os.path.join("/usr/local/xybot/plugins", plugin_id, "config.toml")
            ]
            possible_paths.extend(docker_paths)

        # 查找第一个存在的路径
        for path in possible_paths:
            if os.path.exists(path):
                logger.debug(f"找到插件配置文件: {path}")
                return path

        # 如果没有找到存在的文件，返回默认路径
        # 如果有找到插件目录，使用第一个找到的目录
        if plugin_dirs:
            return os.path.join("plugins", plugin_dirs[0], "config.toml")

        # 否则使用插件ID作为目录名
        return os.path.join("plugins", plugin_id, "config.toml")

    # API: 获取插件配置（可视化 schema + 原文）
    @app.get("/api/plugin_config", response_class=JSONResponse)
    async def api_get_plugin_config(plugin_id: str, request: Request, username: str = Depends(require_auth)):
        try:
            from admin.services.config_service import load_generic_config_view

            config_path = find_plugin_config_path(plugin_id)
            path = Path(config_path) if config_path else Path("plugins") / plugin_id / "config.toml"

            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n",
                    encoding="utf-8",
                )
                logger.info(f"创建了新的插件配置文件: {path}")

            view = load_generic_config_view(path, title=f"插件 {plugin_id}")
            return {
                "success": True,
                "config": view["values"],  # 兼容旧调用
                "data": view["values"],
                "values": view["values"],
                "schema": view["schema"],
                "raw": view["raw"],
                "path": view["path"],
                "config_file": str(path),
            }
        except Exception as e:
            logger.error(f"获取插件配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件配置文件路径
    @app.get("/api/plugin_config_file", response_class=JSONResponse)
    async def api_get_plugin_config_file(plugin_id: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            # 查找配置文件路径
            config_path = find_plugin_config_path(plugin_id)
            if not config_path:
                # 如果配置文件不存在，返回默认位置
                # 如插件尚未创建配置文件，返回它应该创建的位置
                config_path = os.path.join("plugins", plugin_id, "config.toml")

            # 检查文件是否存在，如果不存在则创建一个空的配置文件
            if not os.path.exists(config_path):
                try:
                    # 确保目录存在
                    os.makedirs(os.path.dirname(config_path), exist_ok=True)
                    # 创建空的配置文件
                    with open(config_path, 'w', encoding='utf-8') as f:
                        f.write("# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n")
                    logger.info(f"创建了新的插件配置文件: {config_path}")
                except Exception as e:
                    logger.error(f"创建插件配置文件失败: {str(e)}")

            # 转换为相对路径，以便在文件管理器中打开
            relative_path = os.path.normpath(config_path)

            return {
                "success": True,
                "config_file": relative_path
            }
        except Exception as e:
            logger.error(f"获取插件配置文件路径失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件README.md内容
    @app.get("/api/plugin_readme", response_class=JSONResponse)
    async def api_get_plugin_readme(plugin_id: str, request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            # 查找插件目录
            plugin_dir = None
            readme_path = None

            # 首先检查是否有与插件名相同的目录
            if os.path.isdir(f"plugins/{plugin_id}") and os.path.exists(f"plugins/{plugin_id}/README.md"):
                plugin_dir = plugin_id
                readme_path = f"plugins/{plugin_id}/README.md"
            else:
                # 遍历所有插件目录
                for dirname in os.listdir("plugins"):
                    if os.path.isdir(f"plugins/{dirname}"):
                        # 检查目录中是否有与插件同名的类
                        if os.path.exists(f"plugins/{dirname}/main.py"):
                            try:
                                # 先检查文件内容是否包含插件类名
                                with open(f"plugins/{dirname}/main.py", "r", encoding="utf-8") as f:
                                    content = f.read()
                                    if f"class {plugin_id}" in content:
                                        plugin_dir = dirname
                                        readme_path = f"plugins/{dirname}/README.md"
                                        if os.path.exists(readme_path):
                                            break

                                # 如果没找到，再尝试加载模块检查
                                if not plugin_dir:
                                    module = importlib.import_module(f"plugins.{dirname}.main")
                                    for name, obj in inspect.getmembers(module):
                                        if (inspect.isclass(obj) and
                                            issubclass(obj, PluginBase) and
                                            obj != PluginBase and
                                            obj.__name__ == plugin_id):
                                            # 找到了插件目录，检查README.md
                                            plugin_dir = dirname
                                            readme_path = f"plugins/{dirname}/README.md"
                                            break
                            except Exception as e:
                                logger.error(f"检查插件{plugin_id}的README.md时出错: {str(e)}")

            if not plugin_dir:
                return {"success": False, "message": f"找不到插件 {plugin_id} 的目录"}

            if not readme_path or not os.path.exists(readme_path):
                return {"success": False, "message": f"插件 {plugin_id} 的README.md文件不存在"}

            # 读取README.md内容
            with open(readme_path, "r", encoding="utf-8") as f:
                readme_content = f.read()

            return {
                "success": True,
                "readme": readme_content,
                "plugin_id": plugin_id,
                "plugin_dir": plugin_dir
            }
        except Exception as e:
            logger.error(f"获取插件README.md失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 保存插件配置（可视化/原文）
    @app.post("/api/save_plugin_config", response_class=JSONResponse)
    async def api_save_plugin_config(request: Request, username: str = Depends(require_auth)):
        try:
            from admin.services.config_service import (
                load_generic_config_view,
                save_generic_config_raw,
                save_generic_config_values,
            )

            data = await request.json()
            plugin_id = data.get("plugin_id")
            if not plugin_id:
                return {"success": False, "message": "缺少必要参数 plugin_id"}

            config_path = find_plugin_config_path(plugin_id)
            path = Path(config_path) if config_path else Path("plugins") / plugin_id / "config.toml"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(
                    "# 插件配置文件\n\n[basic]\n# 是否启用插件\nenable = true\n",
                    encoding="utf-8",
                )

            mode = data.get("mode")
            if mode == "raw" or (isinstance(data.get("content"), str) and "values" not in data and "config" not in data):
                content = data.get("content")
                if not isinstance(content, str) or not content.strip():
                    return {"success": False, "message": "配置内容不能为空"}
                result = save_generic_config_raw(path, content)
            else:
                payload = None
                if isinstance(data.get("values"), dict):
                    payload = data["values"]
                elif isinstance(data.get("config"), dict):
                    payload = data["config"]  # 兼容旧结构化保存
                elif isinstance(data.get("config"), str):
                    result = save_generic_config_raw(path, data["config"])
                    payload = None
                else:
                    return {"success": False, "message": "缺少配置数据"}

                if payload is not None:
                    result = save_generic_config_values(path, payload)

            view = load_generic_config_view(path, title=f"插件 {plugin_id}")
            return {
                "success": True,
                "message": "配置已保存",
                "backup": result.get("backup"),
                "path": str(path),
                "data": view["values"],
                "schema": view["schema"],
                "raw": view["raw"],
            }
        except Exception as e:
            logger.error(f"保存插件配置失败: {str(e)}")
            return {"success": False, "error": str(e)}

    @app.get("/api/plugin_market/categories", response_class=JSONResponse)
    async def api_get_plugin_categories(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            categories = await _get_merged_categories()
            if categories:
                logger.info(f"成功获取插件分类列表，共 {len(categories)} 个分类")
                return {"success": True, "categories": categories}
            return await get_default_categories()
        except Exception as e:
            logger.error(f"获取插件分类失败: {str(e)}")
            # 返回默认分类
            return await get_default_categories()

    # 获取默认分类列表（当远程API不可用时使用）
    async def get_default_categories():
        return {
            "success": True,
            "categories": [
                {
                    "id": 1,
                    "value": "all",
                    "label": "全部",
                    "icon": "bi-grid-3x3-gap-fill",
                    "description": "所有插件",
                    "sort_order": 0,
                    "is_active": True
                },
                {
                    "id": 2,
                    "value": "tools",
                    "label": "工具",
                    "icon": "bi-tools",
                    "description": "工具类插件",
                    "sort_order": 1,
                    "is_active": True
                },
                {
                    "id": 3,
                    "value": "ai",
                    "label": "AI",
                    "icon": "bi-cpu",
                    "description": "AI 相关插件",
                    "sort_order": 2,
                    "is_active": True
                },
                {
                    "id": 4,
                    "value": "entertainment",
                    "label": "娱乐",
                    "icon": "bi-controller",
                    "description": "娱乐类插件",
                    "sort_order": 3,
                    "is_active": True
                },
                {
                    "id": 5,
                    "value": "adapter",
                    "label": "适配器",
                    "icon": "bi-plug",
                    "description": "适配器插件",
                    "sort_order": 4,
                    "is_active": True
                },
                {
                    "id": 6,
                    "value": "other",
                    "label": "其他",
                    "icon": "bi-three-dots",
                    "description": "其他类型插件",
                    "sort_order": 5,
                    "is_active": True
                }
            ]
        }

    def _plugin_market_base_urls():
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

    def _market_source_id(base_url: str) -> str:
        lowered = (base_url or "").lower()
        if "v.sxkiss.top" in lowered:
            return "sxkiss"
        if "xianan.xin" in lowered:
            return "xbot"
        parsed = urlparse(lowered)
        host = parsed.netloc or parsed.path
        safe = re.sub(r"[^a-z0-9]+", "_", host).strip("_")
        return safe or "market"

    def _build_market_url(base_url: str, path: str) -> str:
        base = (base_url or "").rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}{path}"

    def _market_headers():
        bot_version = get_bot_version()
        return {
            "X-Client-ID": get_client_id(),
            "X-Bot-Version": bot_version,
            "User-Agent": f"XYBot/{bot_version}",
        }

    def _market_cache_path() -> str:
        return os.path.join(current_dir, "plugin_market_cache.json")

    def _load_market_cache():
        cache_path = _market_cache_path()
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "plugins" in data:
                    return data
                if "data" in data and isinstance(data["data"], dict):
                    return {"plugins": data["data"].get("plugins", [])}
            if isinstance(data, list):
                return {"plugins": data}
        except Exception as e:
            logger.error(f"读取插件市场缓存失败: {e}")
        return None

    def _write_market_cache(plugins, meta=None):
        cache_path = _market_cache_path()
        payload = {
            "plugins": plugins,
            "cached_at": datetime.now().isoformat(),
        }
        if meta:
            payload["sources"] = meta.get("sources", [])
            payload["partial"] = meta.get("partial", False)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"写入插件市场缓存失败: {e}")

    def _normalize_tags(tags):
        if tags is None:
            return []
        if isinstance(tags, list):
            if tags and isinstance(tags[0], dict) and "name" in tags[0]:
                return [t.get("name") for t in tags if isinstance(t, dict) and t.get("name")]
            return [t for t in tags if t is not None]
        if isinstance(tags, str):
            return [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, dict):
            return [v for v in tags.values() if v is not None]
        return []

    def _normalize_plugin(raw_plugin, source_id: str):
        if not isinstance(raw_plugin, dict):
            return None
        tags = _normalize_tags(raw_plugin.get("tags"))
        update_time = (
            raw_plugin.get("update_time")
            or raw_plugin.get("updated_at")
            or raw_plugin.get("updateTime")
            or raw_plugin.get("update_at")
            or raw_plugin.get("submitted_at")
        )
        return {
            "id": raw_plugin.get("id") or raw_plugin.get("plugin_id") or raw_plugin.get("name"),
            "name": raw_plugin.get("name") or "Unknown Plugin",
            "version": raw_plugin.get("version") or "1.0.0",
            "description": raw_plugin.get("description") or "",
            "author": raw_plugin.get("author") or "未知作者",
            "tags": tags,
            "category": raw_plugin.get("category") or raw_plugin.get("type") or "other",
            "github_url": raw_plugin.get("github_url") or raw_plugin.get("github") or "",
            "update_time": update_time or datetime.now().isoformat(),
            "requirements": raw_plugin.get("requirements", []),
            "source": source_id,
        }

    def _build_dedupe_key(plugin):
        github_url = (plugin.get("github_url") or "").strip().lower()
        if github_url:
            return f"url:{github_url}"
        name = (plugin.get("name") or "").strip().lower()
        if name:
            return f"name:{name}"
        plugin_id = (plugin.get("id") or "").strip()
        return f"id:{plugin_id}"

    def _version_key(value):
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        raw = raw.lstrip("vV")
        nums = [int(n) for n in re.findall(r"\d+", raw)]
        return {"raw": raw, "nums": nums}

    def _compare_versions(version_a, version_b):
        if not version_a and not version_b:
            return 0
        key_a = _version_key(version_a)
        key_b = _version_key(version_b)
        nums_a = key_a["nums"] if key_a else []
        nums_b = key_b["nums"] if key_b else []

        if nums_a and nums_b:
            max_len = max(len(nums_a), len(nums_b))
            for idx in range(max_len):
                part_a = nums_a[idx] if idx < len(nums_a) else 0
                part_b = nums_b[idx] if idx < len(nums_b) else 0
                if part_a != part_b:
                    return 1 if part_a > part_b else -1
        elif nums_a and not nums_b:
            return 1
        elif nums_b and not nums_a:
            return -1

        raw_a = "" if version_a is None else str(version_a)
        raw_b = "" if version_b is None else str(version_b)
        if raw_a == raw_b:
            return 0
        return 1 if raw_a > raw_b else -1

    def _parse_update_time(value):
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except Exception:
            return None

    def _is_newer_by_time(candidate, existing):
        dt_candidate = _parse_update_time(candidate)
        dt_existing = _parse_update_time(existing)
        if not dt_candidate or not dt_existing:
            return False
        return dt_candidate > dt_existing

    def _merge_and_dedupe_plugins(plugins):
        merged = {}
        for plugin in plugins:
            if not plugin:
                continue
            key = _build_dedupe_key(plugin)
            current = merged.get(key)
            if not current:
                merged[key] = plugin
                continue
            cmp_result = _compare_versions(plugin.get("version"), current.get("version"))
            if cmp_result > 0:
                merged[key] = plugin
                continue
            if cmp_result == 0 and _is_newer_by_time(plugin.get("update_time"), current.get("update_time")):
                merged[key] = plugin
        merged_list = list(merged.values())
        merged_list.sort(key=lambda item: ((item.get("name") or "").lower(), (item.get("github_url") or "").lower()))
        return merged_list

    async def _fetch_market_plugins(session: aiohttp.ClientSession, base_url: str, headers: dict):
        url = _build_market_url(base_url, PLUGIN_MARKET_API["LIST"])
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return {"success": False, "error": f"{response.status} - {error_text}", "base_url": base_url}
                data = await response.json()
        except Exception as e:
            return {"success": False, "error": str(e), "base_url": base_url}

        if isinstance(data, dict):
            plugins = data.get("plugins", [])
        elif isinstance(data, list):
            plugins = data
        else:
            plugins = []
        return {"success": True, "plugins": plugins, "base_url": base_url}

    async def _fetch_market_categories(session: aiohttp.ClientSession, base_url: str, headers: dict):
        url = _build_market_url(base_url, PLUGIN_MARKET_API["CATEGORIES"])
        try:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status != 200:
                    return {"success": False, "error": f"{response.status}", "base_url": base_url}
                data = await response.json()
        except Exception as e:
            return {"success": False, "error": str(e), "base_url": base_url}

        categories = []
        if isinstance(data, dict):
            categories = data.get("categories", [])
        elif isinstance(data, list):
            categories = data
        return {"success": True, "categories": categories, "base_url": base_url}

    async def _get_merged_market_plugins():
        base_urls = _plugin_market_base_urls()
        if not base_urls:
            return [], {"success_count": 0, "sources": [], "partial": True}

        async with aiohttp.ClientSession() as session:
            headers = _market_headers()
            tasks = [
                _fetch_market_plugins(session, base_url, headers)
                for base_url in base_urls
            ]
            results = await asyncio.gather(*tasks)

        all_plugins = []
        sources = []
        success_count = 0
        for result in results:
            source_id = _market_source_id(result.get("base_url"))
            if result.get("success"):
                success_count += 1
                normalized = [
                    _normalize_plugin(plugin, source_id)
                    for plugin in result.get("plugins", [])
                ]
                all_plugins.extend([p for p in normalized if p])
                sources.append(
                    {
                        "id": source_id,
                        "base_url": result.get("base_url"),
                        "count": len(result.get("plugins", [])),
                    }
                )
            else:
                sources.append(
                    {
                        "id": source_id,
                        "base_url": result.get("base_url"),
                        "error": result.get("error", "unknown error"),
                    }
                )

        merged = _merge_and_dedupe_plugins(all_plugins)
        meta = {
            "success_count": success_count,
            "sources": sources,
            "partial": success_count != len(base_urls),
        }
        return merged, meta

    async def _get_merged_categories():
        base_urls = _plugin_market_base_urls()
        if not base_urls:
            return None
        async with aiohttp.ClientSession() as session:
            headers = _market_headers()
            tasks = [
                _fetch_market_categories(session, base_url, headers)
                for base_url in base_urls
            ]
            results = await asyncio.gather(*tasks)

        categories = {}
        for result in results:
            if not result.get("success"):
                continue
            for category in result.get("categories", []):
                if not isinstance(category, dict):
                    continue
                key = str(category.get("value") or category.get("label") or category.get("id") or "").lower()
                if not key:
                    continue
                if key in categories:
                    continue
                categories[key] = category

        merged = list(categories.values())
        merged.sort(key=lambda item: item.get("sort_order", 0))
        return merged

    # API: 提交插件到市场
    @app.get("/api/plugin_market", response_class=JSONResponse)
    async def api_get_plugin_market(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            merged_plugins, meta = await _get_merged_market_plugins()
            if meta["success_count"] > 0:
                _write_market_cache(merged_plugins, meta)
                return {
                    "success": True,
                    "plugins": merged_plugins,
                    "partial": meta["partial"],
                    "sources": meta["sources"],
                }

            cache_data = _load_market_cache()
            if cache_data:
                return {
                    "success": True,
                    "plugins": cache_data.get("plugins", []),
                    "cached": True,
                    "partial": True,
                    "sources": meta["sources"],
                }
            return {"success": False, "error": "无法连接到插件市场服务器，且无本地缓存", "sources": meta["sources"]}
        except Exception as e:
            logger.error(f"获取插件市场失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 获取插件市场列表 (新路径)
    @app.get("/api/plugin_market/list", response_class=JSONResponse)
    async def api_get_plugin_market_list(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            merged_plugins, meta = await _get_merged_market_plugins()
            if meta["success_count"] > 0:
                _write_market_cache(merged_plugins, meta)
                return {
                    "success": True,
                    "plugins": merged_plugins,
                    "partial": meta["partial"],
                    "sources": meta["sources"],
                }

            cache_data = _load_market_cache()
            if cache_data:
                return {
                    "success": True,
                    "plugins": cache_data.get("plugins", []),
                    "cached": True,
                    "partial": True,
                    "sources": meta["sources"],
                }
            return {"success": False, "error": "无法连接到插件市场服务器，且无本地缓存", "sources": meta["sources"]}
        except Exception as e:
            logger.error(f"获取插件市场失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # API: 提交插件到市场
    @app.post("/api/plugin_market/submit", response_class=JSONResponse)
    async def api_submit_plugin(request: Request, username: str = Depends(require_auth)):
        # 检查认证状态
        try:
            # 从请求中获取JSON数据
            data = await request.json()

            # 准备提交数据
            plugin_data = {
                "name": data.get("name"),
                "description": data.get("description"),
                "author": data.get("author"),
                "version": data.get("version"),
                "github_url": data.get("github_url"),
                "tags": data.get("tags", []),
                "requirements": data.get("requirements", []),
                "submitted_by": username,  # 记录提交者
                "submitted_at": datetime.now().isoformat(),  # 记录提交时间
                "status": "pending"  # 状态：pending, approved, rejected
            }

            # 处理图标（如果有）
            if "icon" in data and data["icon"]:
                plugin_data["icon"] = data["icon"]
            base_urls = _plugin_market_base_urls()
            if not base_urls:
                return {"success": False, "error": "未配置插件市场地址"}

            headers = _market_headers()
            headers["Content-Type"] = "application/json"
            results = {}
            success_count = 0

            async with aiohttp.ClientSession() as session:
                submit_tasks = []
                source_ids = []
                for base_url in base_urls:
                    source_id = _market_source_id(base_url)
                    source_ids.append(source_id)
                    submit_tasks.append(
                        session.post(
                            _build_market_url(base_url, PLUGIN_MARKET_API["SUBMIT"]),
                            json=plugin_data,
                            headers=headers,
                            timeout=30,
                        )
                    )
                responses = await asyncio.gather(*submit_tasks, return_exceptions=True)

                for index, response in enumerate(responses):
                    source_id = source_ids[index]
                    base_url = base_urls[index]
                    if isinstance(response, Exception):
                        error_text = str(response)
                        logger.error(f"提交插件到市场失败 [{source_id}]: {error_text}")
                        results[source_id] = {"success": False, "base_url": base_url, "error": error_text}
                        continue
                    async with response:
                        if response.status == 200:
                            try:
                                resp_data = await response.json(content_type=None)
                            except Exception:
                                resp_data = {}
                            results[source_id] = {
                                "success": True,
                                "base_url": base_url,
                                "id": resp_data.get("id") if isinstance(resp_data, dict) else None,
                            }
                            success_count += 1
                        else:
                            error_text = await response.text()
                            logger.warning(f"提交插件失败 [{source_id}]: {response.status} - {error_text}")
                            results[source_id] = {
                                "success": False,
                                "base_url": base_url,
                                "error": f"{response.status} - {error_text}",
                            }

            failed_sources = [
                source_id for source_id, result in results.items() if not result.get("success")
            ]
            if failed_sources:
                safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", plugin_data.get("name") or "plugin")
                for source_id in failed_sources:
                    source_pending_dir = os.path.join(PLUGIN_MARKET_API["PENDING_DIR"], source_id)
                    os.makedirs(source_pending_dir, exist_ok=True)
                    temp_file = os.path.join(
                        source_pending_dir,
                        f"{int(time.time())}_{safe_name}.json",
                    )
                    payload = {
                        "plugin_data": plugin_data,
                        "source_id": source_id,
                        "base_url": results[source_id].get("base_url"),
                    }
                    with open(temp_file, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)

            if success_count > 0:
                return {
                    "success": True,
                    "message": "插件已提交到插件市场",
                    "partial": success_count != len(base_urls),
                    "results": results,
                }
            return {"success": False, "error": "两个插件市场提交均失败", "results": results}
        except Exception as e:
            logger.error(f"提交插件失败: {str(e)}\n{traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    # API: 安装插件
    @app.post("/api/plugin_market/install", response_class=JSONResponse)
    async def api_install_plugin_from_market(request: Request, username: str = Depends(require_auth)):
        """从插件市场安装插件"""
        try:
            from admin.services import PluginInstaller

            # 获取请求数据
            data = await request.json()
            plugin_data = data.get('plugin_data', {})
            plugin_name = plugin_data.get('name')
            github_url = plugin_data.get('github_url')
            install_dependencies = bool(data.get('install_dependencies', False))

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 使用 PluginInstaller 服务
            installer = PluginInstaller()
            result = installer.install_plugin(
                plugin_name=plugin_name,
                github_url=github_url,
                install_dependencies=install_dependencies
            )

            # 如果安装成功，尝试自动加载插件
            if result.get("success"):
                try:
                    from utils.plugin_manager import plugin_manager
                    bot_instance = getattr(app.state, 'bot_instance', None)
                    if bot_instance:
                        await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)
                except Exception as e:
                    logger.warning(f"自动加载插件失败，用户需要手动启用: {str(e)}")

            return result

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 周期性任务：同步本地待审核插件到服务器
    async def sync_pending_plugins():
        """检查本地待审核插件并尝试同步到服务器"""
        try:
            temp_dir = PLUGIN_MARKET_API["PENDING_DIR"]
            if not os.path.exists(temp_dir):
                return

            base_urls = _plugin_market_base_urls()
            base_url_map = {_market_source_id(url): url for url in base_urls}
            headers = _market_headers()
            headers["Content-Type"] = "application/json"

            async with aiohttp.ClientSession() as session:
                for source_id in os.listdir(temp_dir):
                    source_dir = os.path.join(temp_dir, source_id)
                    if not os.path.isdir(source_dir):
                        continue
                    for filename in os.listdir(source_dir):
                        if not filename.endswith(".json"):
                            continue

                        file_path = os.path.join(source_dir, filename)
                        with open(file_path, "r", encoding="utf-8") as f:
                            payload = json.load(f)
                        plugin_data = payload.get("plugin_data", payload)
                        base_url = payload.get("base_url") or base_url_map.get(source_id)
                        if not base_url:
                            logger.warning(f"待同步插件缺少目标市场: {file_path}")
                            continue

                        try:
                            url = _build_market_url(base_url, PLUGIN_MARKET_API["SUBMIT"])
                            logger.info(f"正在同步插件到服务器: {url}")

                            async with session.post(
                                url,
                                json=plugin_data,
                                headers=headers,
                                timeout=10,
                                allow_redirects=True,
                            ) as response:
                                if response.status == 200:
                                    os.remove(file_path)
                                    logger.info(f"成功同步插件到服务器: {plugin_data.get('name')}")
                        except Exception as e:
                            logger.error(f"同步插件到服务器失败: {e}")
                            continue
        except Exception as e:
            logger.error(f"同步待审核插件失败: {str(e)}")

    # 周期性任务：缓存插件市场数据
    async def cache_plugin_market():
        """从远程服务器缓存插件市场数据到本地"""
        try:
            merged_plugins, meta = await _get_merged_market_plugins()
            if meta["success_count"] > 0:
                _write_market_cache(merged_plugins, meta)
                logger.info(f"成功缓存插件市场数据，共{len(merged_plugins)}个插件")
            else:
                logger.warning("缓存插件市场数据失败：无可用市场响应")
        except Exception as e:
            logger.error(f"缓存插件市场任务失败: {str(e)}")

    # 缓存插件市场数据
    @app.on_event("startup")
    async def startup_cache_plugin_market():
        # 应用启动时缓存一次插件市场数据
        asyncio.create_task(cache_plugin_market())

        # 设置定时任务每小时更新一次缓存
        async def periodic_cache():
            while True:
                await asyncio.sleep(3600)  # 每小时执行一次
                await cache_plugin_market()

        # 设置定时任务每10分钟同步一次待审核插件
        async def periodic_sync():
            while True:
                await asyncio.sleep(600)  # 每10分钟执行一次
                await sync_pending_plugins()

        # 启动定时任务
        asyncio.create_task(periodic_cache())
        asyncio.create_task(periodic_sync())

    # 插件市场API配置
    # 说明：支持双市场聚合，默认读取 v.sxkiss.top 与 xbot（xianan.xin:1562/api）。
    PLUGIN_MARKET_API = {
        "BASE_URLS": _plugin_market_base_urls(),
        "LIST": "/plugins/?status=approved",  # 添加尾部斜杠，避免重定向
        "CATEGORIES": "/categories",
        "SUBMIT": "/plugins/",
        "DETAIL": "/plugins/",
        "INSTALL": "/plugins/install/",
        "CACHE_DIR": os.path.join(current_dir, "cache"),
        "PENDING_DIR": os.path.join(current_dir, "pending_plugins"),
    }
    os.makedirs(PLUGIN_MARKET_API["CACHE_DIR"], exist_ok=True)
    os.makedirs(PLUGIN_MARKET_API["PENDING_DIR"], exist_ok=True)

    # 辅助函数：获取客户端ID
    def get_client_id():
        """获取或生成唯一的客户端ID"""
        cache_dir = PLUGIN_MARKET_API["CACHE_DIR"]
        os.makedirs(cache_dir, exist_ok=True)
        client_id_file = os.path.join(cache_dir, "client_id")

        # 如果文件存在，读取ID
        if os.path.exists(client_id_file):
            try:
                with open(client_id_file, "r") as f:
                    return f.read().strip()
            except Exception as e:
                logger.warning(f"读取客户端ID失败: {e}")

        # 生成新ID
        import uuid
        client_id = str(uuid.uuid4())

        # 保存到文件
        try:
            with open(client_id_file, "w") as f:
                f.write(client_id)
        except Exception as e:
            logger.warning(f"保存客户端ID失败: {e}")

        return client_id

    # 辅助函数：获取Bot版本
    def get_bot_version():
        """获取Bot版本信息"""
        try:
            # 尝试从配置文件读取版本
            config_file = os.path.join(os.path.dirname(current_dir), "main_config.toml")
            if os.path.exists(config_file):
                try:
                    import tomllib
                except ImportError:
                    import tomli as tomllib
                with open(config_file, "rb") as f:
                    config = tomllib.load(f)
                    return config.get("version", "1.0.0")
        except Exception as e:
            logger.debug(f"读取版本信息失败: {e}")
        return "1.0.0"

    # API: 安装插件
    @app.post("/api/plugins/install", response_class=JSONResponse)
    async def api_install_plugin_direct(request: Request, username: str = Depends(require_auth)):
        """直接安装插件（通过 GitHub URL）"""
        try:
            from admin.services import PluginInstaller

            # 获取请求数据
            data = await request.json()
            plugin_name = data.get('name')
            github_url = data.get('github_url')
            install_dependencies = bool(data.get('install_dependencies', False))

            if not plugin_name or not github_url:
                return {"success": False, "error": "缺少必要参数"}

            # 使用 PluginInstaller 服务
            installer = PluginInstaller()
            result = installer.install_plugin(
                plugin_name=plugin_name,
                github_url=github_url,
                install_dependencies=install_dependencies
            )

            # 如果安装成功，尝试自动加载插件
            if result.get("success"):
                try:
                    from utils.plugin_manager import plugin_manager
                    bot_instance = getattr(app.state, 'bot_instance', None)
                    if bot_instance:
                        success = await plugin_manager.load_plugin_from_directory(bot_instance, plugin_name)
                        if not success:
                            result["message"] = f"插件 {plugin_name} 安装成功，但加载失败"
                except Exception as e:
                    logger.warning(f"自动加载插件失败: {str(e)}")
                    result["message"] = f"插件 {plugin_name} 安装成功，但自动加载失败"

            return result

        except Exception as e:
            logger.error(f"安装插件失败: {str(e)}")
            return {"success": False, "error": str(e)}

    # 注释掉重复的API端点定义
    #@app.get('/api/system/info')
    #async def system_info_api(request: Request, username: str = Depends(require_auth)):
    #    """系统信息API"""
    #    try:
    #        info = get_system_info()
    #        return JSONResponse(content={
    #            "success": True,
    #            "data": info,
    #            "error": None
    #        })
    #    except Exception as e:
    #        logger.error(f"获取系统信息API失败: {str(e)}")
    #        return JSONResponse(content={
    #            "success": False,
    #            "data": {
    #                "hostname": "unknown",
    #                "platform": "unknown",
    #                "python_version": "unknown",
    #                "cpu_count": 0,
    #                "memory_total": 0,
    #                "memory_available": 0,
    #                "disk_total": 0,
    #                "disk_free": 0,
    #                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    #            },
    #            "error": str(e)
    #        })

    def check_auth(request: Request):
        """检查用户认证状态"""
        try:
            token = request.headers.get('Authorization')
            if not token:
                # 尝试从cookie中获取token
                token = request.cookies.get('token')

            if not token:
                raise HTTPException(status_code=401, detail="未登录或登录已过期")

            # 这里可以添加token验证的逻辑
            # 例如验证token的有效性，检查是否过期等
            # 如果验证失败，抛出HTTPException(status_code=401)

            return True
        except Exception as e:
            logger.error(f"认证检查失败: {str(e)}")
            raise HTTPException(status_code=401, detail="认证失败")

    from fastapi import WebSocket
    from utils.plugin_manager import plugin_manager

    # WebSocket连接管理
    class ConnectionManager:
        def __init__(self):
            self.active_connections: List[WebSocket] = []

        async def connect(self, websocket: WebSocket, username: str = Depends(require_auth)):
            await websocket.accept()
            self.active_connections.append(websocket)

        def disconnect(self, websocket: WebSocket):
            self.active_connections.remove(websocket)

        async def send_message(self, message: str, websocket: WebSocket, username: str = Depends(require_auth)):
            await websocket.send_text(message)

    manager = ConnectionManager()

    @app.websocket("/ws/plugins")
    async def websocket_endpoint(websocket: WebSocket, username: str = Depends(require_auth)):
        await manager.connect(websocket)
        try:
            while True:
                data = await websocket.receive_json()
                if data["action"] == "install_plugin":
                    plugin_data = data["data"]
                    try:
                        # 获取DependencyManager插件实例
                        dependency_manager = None
                        for plugin in plugin_manager.plugins:
                            if plugin.__class__.__name__ == "DependencyManager":
                                dependency_manager = plugin
                                break

                        if not dependency_manager:
                            await websocket.send_json({
                                "type": "install_complete",
                                "success": False,
                                "error": "DependencyManager插件未安装"
                            })
                            continue

                        # 发送进度消息
                        await websocket.send_json({
                            "type": "install_progress",
                            "message": "开始安装插件..."
                        })

                        # 使用DependencyManager的安装方法
                        await dependency_manager._handle_github_install(
                            bot_instance,
                            "admin",  # 使用admin作为chat_id
                            plugin_data["github_url"]
                        )

                        # 发送完成消息
                        await websocket.send_json({
                            "type": "install_complete",
                            "success": True
                        })

                    except Exception as e:
                        logger.error(f"安装插件失败: {str(e)}")
                        await websocket.send_json({
                            "type": "install_complete",
                            "success": False,
                            "error": str(e)
                        })
        except WebSocketDisconnect:
            manager.disconnect(websocket)
