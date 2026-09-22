"""
处理DOW框架插件的模块
"""
import os
import sys
import logging
import json
import glob
import re
from pathlib import Path

# 设置日志
logger = logging.getLogger("dow_plugins")

def extract_plugin_info_from_module(module_path, default_name="未知插件"):
    """从Python模块中提取插件信息"""
    try:
        # 获取模块名称
        module_name = os.path.basename(module_path).replace(".py", "")
        logger.info(f"尝试从模块提取插件信息: {module_path}, 模块名: {module_name}")

        # 直接读取文件内容，而不是执行模块
        with open(module_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 初始化插件信息
        plugin_info = {
            "name": default_name,
            "description": "",
            "author": "",
            "version": "1.0.0"
        }

        # 从文件内容中提取信息
        # 查找常见的插件信息模式

        # 查找类定义
        class_pattern = r"class\s+(\w+).*?:"
        class_matches = re.findall(class_pattern, content)
        logger.info(f"找到的类: {class_matches}")

        # 查找name属性
        name_pattern = r"name\s*=\s*['\"](.+?)['\"]"
        name_matches = re.findall(name_pattern, content)
        if name_matches:
            plugin_info["name"] = name_matches[0]
            logger.info(f"找到name属性: {plugin_info['name']}")

        # 查找desc或description属性
        desc_pattern = r"desc(?:ription)?\s*=\s*['\"](.+?)['\"]"
        desc_matches = re.findall(desc_pattern, content)
        if desc_matches:
            plugin_info["description"] = desc_matches[0]
            logger.info(f"找到description属性: {plugin_info['description']}")

        # 查找author属性
        author_pattern = r"author\s*=\s*['\"](.+?)['\"]"
        author_matches = re.findall(author_pattern, content)
        if author_matches:
            plugin_info["author"] = author_matches[0]
            logger.info(f"找到author属性: {plugin_info['author']}")

        # 查找version属性
        version_pattern = r"version\s*=\s*['\"](.+?)['\"]"
        version_matches = re.findall(version_pattern, content)
        if version_matches:
            plugin_info["version"] = version_matches[0]
            logger.info(f"找到version属性: {plugin_info['version']}")

        # 查找文档字符串
        docstring_pattern = r'"""(.+?)"""'
        docstring_matches = re.findall(docstring_pattern, content, re.DOTALL)
        if docstring_matches and not plugin_info["description"]:
            # 使用第一个文档字符串的第一行作为描述
            first_line = docstring_matches[0].strip().split('\n')[0]
            plugin_info["description"] = first_line
            logger.info(f"使用文档字符串作为描述: {plugin_info['description']}")

        # 如果是__init__.py文件，尝试查找导入的模块
        if os.path.basename(module_path) == "__init__.py":
            # 查找目录中的其他Python文件
            dir_path = os.path.dirname(module_path)
            py_files = [f for f in os.listdir(dir_path) if f.endswith('.py') and f != '__init__.py']
            logger.info(f"目录中的其他Python文件: {py_files}")

            # 如果有其他Python文件，尝试从中提取信息
            if py_files and not (plugin_info["name"] and plugin_info["description"]):
                for py_file in py_files:
                    py_file_path = os.path.join(dir_path, py_file)
                    try:
                        with open(py_file_path, "r", encoding="utf-8") as f:
                            py_content = f.read()

                        # 查找name属性
                        if not plugin_info["name"]:
                            name_matches = re.findall(name_pattern, py_content)
                            if name_matches:
                                plugin_info["name"] = name_matches[0]
                                logger.info(f"从{py_file}找到name属性: {plugin_info['name']}")

                        # 查找desc或description属性
                        if not plugin_info["description"]:
                            desc_matches = re.findall(desc_pattern, py_content)
                            if desc_matches:
                                plugin_info["description"] = desc_matches[0]
                                logger.info(f"从{py_file}找到description属性: {plugin_info['description']}")

                        # 查找author属性
                        if not plugin_info["author"]:
                            author_matches = re.findall(author_pattern, py_content)
                            if author_matches:
                                plugin_info["author"] = author_matches[0]
                                logger.info(f"从{py_file}找到author属性: {plugin_info['author']}")

                        # 查找version属性
                        if not plugin_info["version"]:
                            version_matches = re.findall(version_pattern, py_content)
                            if version_matches:
                                plugin_info["version"] = version_matches[0]
                                logger.info(f"从{py_file}找到version属性: {plugin_info['version']}")

                        # 如果已经找到了所有信息，就不再继续查找
                        if plugin_info["name"] and plugin_info["description"] and plugin_info["author"] and plugin_info["version"]:
                            break
                    except Exception as e:
                        logger.warning(f"读取文件{py_file}失败: {e}")

        # 确保所有字段都有值
        if not plugin_info["name"]:
            plugin_info["name"] = default_name
        if not plugin_info["description"]:
            plugin_info["description"] = f"{default_name}插件"
        if not plugin_info["author"]:
            plugin_info["author"] = "sxkiss"

        logger.info(f"最终提取的插件信息: {plugin_info}")
        return plugin_info
    except Exception as e:
        logger.warning(f"从模块提取插件信息失败: {e}")
        import traceback
        logger.warning(traceback.format_exc())
        return None

def get_dow_plugins():
    """获取DOW框架的插件列表"""
    # 启用调试模式
    debug_mode = os.environ.get("DEBUG_DOW_PLUGINS", "False").lower() in ("true", "1", "yes")

    try:
        logger.info("开始获取DOW框架插件...")

        # 获取当前脚本目录
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # DOW框架路径
        dow_path = os.path.join(os.path.dirname(script_dir), "dow")
        dow_abs_path = os.path.abspath(dow_path)

        logger.info(f"DOW框架路径: {dow_abs_path}")

        # 检查DOW框架是否存在
        if not os.path.exists(dow_abs_path):
            logger.warning(f"DOW框架目录不存在: {dow_abs_path}")
            return generate_mock_plugins() if debug_mode else []

        # 插件目录路径 - 改为 dow/plugins 目录
        plugins_dir = os.path.join(dow_path, "plugins")

        logger.info(f"DOW插件目录路径: {plugins_dir}")

        # 检查插件目录是否存在
        if not os.path.exists(plugins_dir):
            logger.warning(f"DOW插件目录不存在: {plugins_dir}")
            return generate_mock_plugins() if debug_mode else []

        # 尝试获取插件管理器实例（用于找正在使用的插件）
        plugin_manager = None
        try:
            if dow_abs_path not in sys.path:
                sys.path.insert(0, dow_abs_path)
                logger.info(f"已将DOW框架路径添加到sys.path: {dow_abs_path}")

            from dow.plugins.plugin_manager import PluginManager
            plugin_manager = PluginManager()
            logger.info("成功获取DOW框架插件管理器实例")
        except Exception as e:
            logger.warning(f"获取DOW插件管理器实例失败: {e}")
            plugin_manager = None

        # 直接扫描插件目录获取插件
        plugins_info = []
        plugin_id = 0

        for item_name in os.listdir(plugins_dir):
            item_path = os.path.join(plugins_dir, item_name)

            # 只处理目录或Python文件
            if os.path.isfile(item_path) and not item_path.endswith('.py'):
                continue

            # 如果是Python文件，判断它是否是插件文件
            if os.path.isfile(item_path) and item_path.endswith('.py'):
                if item_name == '__init__.py' or item_name == 'plugin_manager.py' or item_name == 'event.py':
                    continue

                # 尝试从Python文件中提取插件信息
                default_name = os.path.splitext(item_name)[0]
                module_info = extract_plugin_info_from_module(item_path, default_name)

                plugin_info = {
                    "id": f"dow_plugin_{plugin_id}",
                    "name": default_name,
                    "enabled": True,  # 默认设为启用
                    "description": "DOW框架插件",
                    "author": "DOW",
                    "version": "1.0.0",
                    "path": item_path,
                    "framework": "dow"
                }

                # 如果成功提取到模块信息，更新插件信息
                if module_info:
                    logger.info(f"成功从Python文件提取到插件信息: {module_info}")
                    plugin_info["name"] = module_info.get("name", default_name)
                    plugin_info["description"] = module_info.get("description", plugin_info["description"])
                    plugin_info["author"] = module_info.get("author", plugin_info["author"])
                    plugin_info["version"] = module_info.get("version", plugin_info["version"])

                # 确保所有字段都有合理的值
                if not plugin_info["name"] or plugin_info["name"] == "DOW框架插件":
                    plugin_info["name"] = default_name
                    logger.info(f"使用文件名作为插件名称: {default_name}")

                if not plugin_info["description"] or plugin_info["description"] == "DOW框架插件":
                    plugin_info["description"] = f"{default_name}插件"
                    logger.info(f"使用默认描述: {plugin_info['description']}")

                if not plugin_info["author"] or plugin_info["author"] == "DOW":
                    plugin_info["author"] = "sxkiss"
                    logger.info(f"使用默认作者: {plugin_info['author']}")

                logger.info(f"最终Python文件插件信息: {plugin_info}")
                plugin_id += 1
                plugins_info.append(plugin_info)
                continue

            # 目录处理
            if not os.path.isdir(item_path):
                continue

            # 跳过非插件目录
            if item_name.startswith('__') or item_name == 'config':
                continue

            # 检查是否是插件目录（包含__init__.py文件或任何.py文件）
            init_file = os.path.join(item_path, "__init__.py")
            has_py_files = any(f.endswith('.py') for f in os.listdir(item_path) if os.path.isfile(os.path.join(item_path, f)))
            config_file = os.path.join(item_path, "config.json")

            is_plugin = os.path.isfile(init_file) or has_py_files

            if not is_plugin:
                continue

            # 尝试从config.json获取插件信息
            plugin_info = {
                "id": f"dow_plugin_{plugin_id}",
                "name": item_name,
                "enabled": True,  # 默认设为启用，因为在DOW/plugins中的插件通常是启用的
                "description": "DOW框架插件",
                "author": "DOW",
                "version": "1.0.0",
                "path": item_path,
                "framework": "dow"
            }
            plugin_id += 1

            # 尝试从__init__.py文件中提取插件信息
            if os.path.isfile(init_file):
                logger.info(f"尝试从__init__.py文件中提取插件信息: {init_file}")
                module_info = extract_plugin_info_from_module(init_file, item_name)
                if module_info:
                    logger.info(f"成功从__init__.py提取到插件信息: {module_info}")
                    plugin_info["name"] = module_info.get("name", item_name)
                    plugin_info["description"] = module_info.get("description", plugin_info["description"])
                    plugin_info["author"] = module_info.get("author", plugin_info["author"])
                    plugin_info["version"] = module_info.get("version", plugin_info["version"])
                else:
                    logger.warning(f"无法从__init__.py提取插件信息: {init_file}")

            # 如果存在config.json，从中获取更多信息
            if os.path.isfile(config_file):
                logger.info(f"尝试从config.json文件中提取插件信息: {config_file}")
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        config_data = json.load(f)

                    logger.info(f"成功加载config.json: {config_data}")

                    # 更新插件信息
                    if "name" in config_data:
                        plugin_info["name"] = config_data["name"]
                        logger.info(f"从config.json获取到插件名称: {config_data['name']}")
                    if "description" in config_data:
                        plugin_info["description"] = config_data["description"]
                        logger.info(f"从config.json获取到插件描述: {config_data['description']}")
                    if "author" in config_data:
                        plugin_info["author"] = config_data["author"]
                        logger.info(f"从config.json获取到插件作者: {config_data['author']}")
                    if "version" in config_data:
                        plugin_info["version"] = config_data["version"]
                        logger.info(f"从config.json获取到插件版本: {config_data['version']}")

                    # 尝试从其他可能的字段获取信息
                    if "desc" in config_data and not plugin_info["description"]:
                        plugin_info["description"] = config_data["desc"]
                        logger.info(f"从config.json的desc字段获取到插件描述: {config_data['desc']}")
                    if "plugin_name" in config_data and not plugin_info["name"]:
                        plugin_info["name"] = config_data["plugin_name"]
                        logger.info(f"从config.json的plugin_name字段获取到插件名称: {config_data['plugin_name']}")
                except Exception as e:
                    logger.warning(f"解析插件 {item_name} 的config.json文件失败: {e}")
                    import traceback
                    logger.warning(traceback.format_exc())

            # 从插件管理器检查插件是否已启用
            if plugin_manager and hasattr(plugin_manager, "plugins"):
                logger.info(f"尝试从插件管理器获取插件信息: {item_name}")
                # 检查是否在已加载的插件中
                for plugin_name, plugin in plugin_manager.plugins.items():
                    plugin_name_attr = getattr(plugin, 'name', '')
                    logger.info(f"检查插件: {plugin_name}, 插件名称属性: {plugin_name_attr}")

                    if plugin_name_attr.lower() == item_name.lower():
                        logger.info(f"在插件管理器中找到匹配的插件: {plugin_name}")
                        plugin_info["enabled"] = getattr(plugin, 'enabled', True)
                        logger.info(f"插件启用状态: {plugin_info['enabled']}")

                        # 使用DOW插件管理器中的其他信息
                        if hasattr(plugin, 'desc') and plugin.desc:
                            plugin_info["description"] = plugin.desc
                            logger.info(f"从插件管理器获取到插件描述: {plugin.desc}")

                        if hasattr(plugin, 'author') and plugin.author:
                            plugin_info["author"] = plugin.author
                            logger.info(f"从插件管理器获取到插件作者: {plugin.author}")

                        if hasattr(plugin, 'version') and plugin.version:
                            plugin_info["version"] = plugin.version
                            logger.info(f"从插件管理器获取到插件版本: {plugin.version}")

                        break

            # 确保所有字段都有合理的值
            if not plugin_info["name"] or plugin_info["name"] == "DOW框架插件":
                plugin_info["name"] = item_name
                logger.info(f"使用目录名作为插件名称: {item_name}")

            if not plugin_info["description"] or plugin_info["description"] == "DOW框架插件":
                plugin_info["description"] = f"{item_name}插件"
                logger.info(f"使用默认描述: {plugin_info['description']}")

            if not plugin_info["author"] or plugin_info["author"] == "DOW":
                plugin_info["author"] = "sxkiss"
                logger.info(f"使用默认作者: {plugin_info['author']}")

            logger.info(f"最终插件信息: {plugin_info}")
            plugins_info.append(plugin_info)

        logger.info(f"成功扫描到 {len(plugins_info)} 个DOW框架插件")

        # 如果没有找到插件但启用了调试模式，返回模拟数据
        if not plugins_info and debug_mode:
            logger.info("没有找到DOW框架插件，使用模拟数据")
            return generate_mock_plugins()

        return plugins_info

    except Exception as e:
        logger.error(f"获取DOW插件信息失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return generate_mock_plugins() if debug_mode else []

def generate_mock_plugins():
    """生成模拟的DOW框架插件数据（用于调试）"""
    logger.info("生成模拟的DOW框架插件数据")

    # 检查是否存在缓存的模拟数据
    mock_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_dow_plugins.json")

    if os.path.exists(mock_file):
        try:
            with open(mock_file, "r", encoding="utf-8") as f:
                mock_plugins = json.load(f)
                logger.info(f"从缓存加载了{len(mock_plugins)}个模拟DOW插件")
                return mock_plugins
        except Exception as e:
            logger.error(f"加载模拟插件数据失败: {e}")

    # 创建默认的模拟插件数据
    mock_plugins = [
        {
            "id": "dow_plugin_0",
            "name": "阅文小说",
            "enabled": True,
            "description": "阅文小说下载插件，支持搜索和下载小说",
            "author": "sxkiss",
            "version": "1.0.0",
            "path": "dow/plugins/yuewen",
            "framework": "dow"
        },
        {
            "id": "dow_plugin_1",
            "name": "通义千问",
            "enabled": True,
            "description": "通义千问AI聊天插件，支持智能对话和图像生成",
            "author": "sxkiss",
            "version": "1.1.0",
            "path": "dow/plugins/TongyiPlugin",
            "framework": "dow"
        },
        {
            "id": "dow_plugin_2",
            "name": "视频下载",
            "enabled": False,
            "description": "视频下载插件，支持多平台视频下载",
            "author": "sxkiss",
            "version": "0.9.0",
            "path": "dow/plugins/video_downloader",
            "framework": "dow"
        }
    ]

    # 保存模拟数据到缓存
    try:
        with open(mock_file, "w", encoding="utf-8") as f:
            json.dump(mock_plugins, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存{len(mock_plugins)}个模拟DOW插件到缓存")
    except Exception as e:
        logger.error(f"保存模拟插件数据失败: {e}")

    return mock_plugins

def get_dow_plugin_readme(plugin_id):
    """获取DOW框架插件的README文件内容"""
    try:
        # 获取插件列表
        plugins = get_dow_plugins()

        # 查找指定ID的插件
        plugin = None
        for p in plugins:
            if p["id"] == plugin_id:
                plugin = p
                break

        if not plugin:
            return {"success": False, "error": f"找不到ID为{plugin_id}的插件"}

        # 获取插件路径
        plugin_path = plugin.get("path")
        if not plugin_path or not os.path.exists(plugin_path):
            return {"success": False, "error": "插件路径无效"}

        # 如果是文件，获取其所在目录
        if os.path.isfile(plugin_path):
            plugin_path = os.path.dirname(plugin_path)

        # 查找README文件（支持多种扩展名和大小写）
        readme_patterns = [
            os.path.join(plugin_path, "README.md"),
            os.path.join(plugin_path, "readme.md"),
            os.path.join(plugin_path, "README.txt"),
            os.path.join(plugin_path, "readme.txt")
        ]

        readme_file = None
        for pattern in readme_patterns:
            if os.path.exists(pattern):
                readme_file = pattern
                break

        # 如果没有找到README文件，尝试查找任何包含"readme"的文件
        if not readme_file:
            readme_candidates = glob.glob(os.path.join(plugin_path, "*readme*"), recursive=False)
            if readme_candidates:
                readme_file = readme_candidates[0]

        # 如果仍然没有找到README文件，返回默认内容
        if not readme_file:
            return {
                "success": True,
                "readme": f"# {plugin['name']}\n\n{plugin['description']}\n\n作者: {plugin['author']}\n\n版本: {plugin['version']}\n\n该插件暂无详细说明文档。"
            }

        # 读取README文件内容
        with open(readme_file, "r", encoding="utf-8") as f:
            readme_content = f.read()

        return {"success": True, "readme": readme_content}

    except Exception as e:
        logger.error(f"获取DOW插件README失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": f"获取README失败: {str(e)}"}

def get_dow_plugin_config_file(plugin_id):
    """获取DOW框架插件的配置文件路径"""
    try:
        # 获取插件列表
        plugins = get_dow_plugins()

        # 查找指定ID的插件
        plugin = None
        for p in plugins:
            if p["id"] == plugin_id:
                plugin = p
                break

        if not plugin:
            return {"success": False, "error": f"找不到ID为{plugin_id}的插件"}

        # 获取插件路径
        plugin_path = plugin.get("path")
        if not plugin_path or not os.path.exists(plugin_path):
            return {"success": False, "error": "插件路径无效"}

        # 如果是文件，获取其所在目录
        if os.path.isfile(plugin_path):
            plugin_path = os.path.dirname(plugin_path)

        # 查找配置文件（支持多种格式）
        config_patterns = [
            os.path.join(plugin_path, "config.toml"),
            os.path.join(plugin_path, "config.json"),
            os.path.join(plugin_path, "config.yaml"),
            os.path.join(plugin_path, "config.yml"),
            os.path.join(plugin_path, "config.ini")
        ]

        config_file = None
        for pattern in config_patterns:
            if os.path.exists(pattern):
                config_file = pattern
                break

        # 如果没有找到配置文件，尝试查找任何包含"config"的文件
        if not config_file:
            config_candidates = glob.glob(os.path.join(plugin_path, "*config*"), recursive=False)
            if config_candidates:
                config_file = config_candidates[0]

        # 如果仍然没有找到配置文件，返回错误
        if not config_file:
            return {"success": False, "error": "找不到配置文件"}

        return {"success": True, "config_file": config_file}

    except Exception as e:
        logger.error(f"获取DOW插件配置文件失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": f"获取配置文件失败: {str(e)}"}

def get_dow_plugin_config_content(plugin_id):
    """获取DOW框架插件的配置文件内容"""
    try:
        # 先获取配置文件路径
        config_file_result = get_dow_plugin_config_file(plugin_id)

        # 如果获取路径失败，直接返回错误
        if not config_file_result.get("success"):
            return config_file_result

        # 获取配置文件路径
        config_file = config_file_result.get("config_file")

        # 如果需要创建配置文件
        if config_file_result.get("needs_creation"):
            # 返回默认内容
            return {
                "success": True,
                "content": config_file_result.get("default_content", ""),
                "config_file": config_file,
                "needs_creation": True,
                "message": config_file_result.get("message", "将创建默认配置文件")
            }

        # 检查文件是否存在
        if not os.path.exists(config_file):
            return {"success": False, "error": f"配置文件不存在: {config_file}"}

        # 读取配置文件内容
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                content = f.read()

            return {
                "success": True,
                "content": content,
                "config_file": config_file
            }
        except Exception as e:
            logger.error(f"读取配置文件内容失败: {e}")
            return {"success": False, "error": f"读取配置文件内容失败: {str(e)}"}

    except Exception as e:
        logger.error(f"获取DOW插件配置文件内容失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"success": False, "error": f"获取配置文件内容失败: {str(e)}"}

# 设置环境变量以启用调试模式（默认启用）
os.environ["DEBUG_DOW_PLUGINS"] = "True"

async def enable_dow_plugin(plugin_id):
    """启用DOW框架插件"""
    try:
        logger.info(f"尝试启用DOW插件: {plugin_id}")

        # 获取插件列表
        plugins = get_dow_plugins()

        # 查找指定ID的插件
        plugin = None
        for p in plugins:
            if p["id"] == plugin_id:
                plugin = p
                break

        if not plugin:
            return False, f"找不到ID为{plugin_id}的插件"

        # 获取插件路径
        plugin_path = plugin.get("path")
        if not plugin_path or not os.path.exists(plugin_path):
            return False, "插件路径无效"

        # 获取插件名称
        plugin_name = plugin.get("name")

        # 尝试获取DOW插件管理器
        try:
            # 获取DOW框架路径
            dow_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dow")
            dow_abs_path = os.path.abspath(dow_path)

            if dow_abs_path not in sys.path:
                sys.path.insert(0, dow_abs_path)
                logger.info(f"已将DOW框架路径添加到sys.path: {dow_abs_path}")

            # 导入DOW插件管理器
            from dow.plugins.plugin_manager import PluginManager
            plugin_manager = PluginManager()

            # 获取配置文件路径
            config_file = os.path.join(plugin_path, "config.json")

            # 如果是文件，获取其所在目录
            if os.path.isfile(plugin_path):
                plugin_path = os.path.dirname(plugin_path)

            # 检查配置文件是否存在
            if os.path.exists(config_file):
                # 读取配置文件
                with open(config_file, "r", encoding="utf-8") as f:
                    config_data = json.load(f)

                # 修改enable字段为true
                if isinstance(config_data, dict):
                    config_data["enable"] = True

                    # 保存配置文件
                    with open(config_file, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=4, ensure_ascii=False)

                    logger.info(f"已更新插件配置文件，设置enable=true: {config_file}")

            # 更新插件状态
            plugin["enabled"] = True

            logger.info(f"DOW插件 {plugin_name} 已启用")
            return True, f"插件 {plugin_name} 已启用"

        except Exception as e:
            logger.error(f"启用DOW插件失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, f"启用插件失败: {str(e)}"

    except Exception as e:
        logger.error(f"启用DOW插件失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False, f"启用插件失败: {str(e)}"

async def disable_dow_plugin(plugin_id):
    """禁用DOW框架插件"""
    try:
        logger.info(f"尝试禁用DOW插件: {plugin_id}")

        # 获取插件列表
        plugins = get_dow_plugins()

        # 查找指定ID的插件
        plugin = None
        for p in plugins:
            if p["id"] == plugin_id:
                plugin = p
                break

        if not plugin:
            return False, f"找不到ID为{plugin_id}的插件"

        # 获取插件路径
        plugin_path = plugin.get("path")
        if not plugin_path or not os.path.exists(plugin_path):
            return False, "插件路径无效"

        # 获取插件名称
        plugin_name = plugin.get("name")

        # 尝试获取DOW插件管理器
        try:
            # 获取DOW框架路径
            dow_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dow")
            dow_abs_path = os.path.abspath(dow_path)

            if dow_abs_path not in sys.path:
                sys.path.insert(0, dow_abs_path)
                logger.info(f"已将DOW框架路径添加到sys.path: {dow_abs_path}")

            # 导入DOW插件管理器
            from dow.plugins.plugin_manager import PluginManager
            plugin_manager = PluginManager()

            # 获取配置文件路径
            config_file = os.path.join(plugin_path, "config.json")

            # 如果是文件，获取其所在目录
            if os.path.isfile(plugin_path):
                plugin_path = os.path.dirname(plugin_path)

            # 检查配置文件是否存在
            if os.path.exists(config_file):
                # 读取配置文件
                with open(config_file, "r", encoding="utf-8") as f:
                    config_data = json.load(f)

                # 修改enable字段为false
                if isinstance(config_data, dict):
                    config_data["enable"] = False

                    # 保存配置文件
                    with open(config_file, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=4, ensure_ascii=False)

                    logger.info(f"已更新插件配置文件，设置enable=false: {config_file}")

            # 更新插件状态
            plugin["enabled"] = False

            logger.info(f"DOW插件 {plugin_name} 已禁用")
            return True, f"插件 {plugin_name} 已禁用"

        except Exception as e:
            logger.error(f"禁用DOW插件失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False, f"禁用插件失败: {str(e)}"

    except Exception as e:
        logger.error(f"禁用DOW插件失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False, f"禁用插件失败: {str(e)}"