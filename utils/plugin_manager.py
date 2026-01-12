import ast
import importlib
import inspect
import os
import sys
import tomllib
import traceback
from typing import Dict, List, Type, Union

from loguru import logger

from WechatAPI import WechatAPIClient

from .event_manager import EventManager
from .plugin_base import PluginBase


class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, PluginBase] = {}
        self.plugin_classes: Dict[str, Type[PluginBase]] = {}
        self.plugin_info: Dict[str, dict] = {}  # 新增：存储所有插件信息

        # 默认将 excluded_plugins 初始化为空列表
        self.excluded_plugins: List[str] = []

        try:
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)

            # 安全地获取 'disabled-plugins' 配置
            # 使用 .get("XYBot", {}).get("disabled-plugins") 防止因键不存在而引发 KeyError
            disabled_plugins_setting = main_config.get("XYBot", {}).get(
                "disabled-plugins"
            )

            if isinstance(disabled_plugins_setting, list):
                # 如果配置值本身已经是列表 (例如，TOML 正确解析了 disabled-plugins = ["p1", "p2"])
                self.excluded_plugins = [str(item) for item in disabled_plugins_setting]
            elif isinstance(disabled_plugins_setting, str):
                if (
                    not disabled_plugins_setting.strip()
                ):  # 处理空字符串或仅包含空白的字符串
                    self.excluded_plugins = []
                else:
                    try:
                        # 尝试将字符串解析为 Python 字面量 (例如 "['p1', 'p2']")
                        parsed_value = ast.literal_eval(disabled_plugins_setting)
                        if isinstance(parsed_value, list):
                            # 解析结果是一个列表，确保所有元素都是字符串
                            self.excluded_plugins = [str(item) for item in parsed_value]
                        elif isinstance(parsed_value, str):
                            # 如果解析结果是一个字符串 (例如，配置是 "'PluginA'")
                            # 那么将其视为包含单个元素的列表
                            self.excluded_plugins = [parsed_value]
                        else:
                            # 解析结果不是列表也不是字符串，记录警告并使用空列表
                            logger.warning(
                                f"配置文件中的 'disabled-plugins' 字符串 '{disabled_plugins_setting}' "
                                f"解析后得到非预期的类型: {type(parsed_value).__name__}。"
                                f"已将禁用插件列表初始化为空。"
                            )
                            self.excluded_plugins = []
                    except (ValueError, SyntaxError):
                        logger.warning(
                            f"无法将配置文件中的 'disabled-plugins' 字符串 '{disabled_plugins_setting}' "
                            f"作为列表字面量解析。将尝试将其视为单个插件名称。"
                        )
                        # 将该字符串视为单个插件名称，放入列表中
                        self.excluded_plugins = [disabled_plugins_setting]
            elif disabled_plugins_setting is not None:
                # 配置值的类型既不是列表也不是字符串，但也不是 None (例如，可能是数字等错误配置)
                logger.warning(
                    f"配置文件中的 'disabled-plugins' 类型为 {type(disabled_plugins_setting).__name__}，"
                    f"期望为列表或代表列表的字符串。已将禁用插件列表初始化为空。"
                )
                self.excluded_plugins = []
            # 如果 disabled_plugins_setting 为 None (例如，键不存在)，self.excluded_plugins 保持为默认的空列表 []

        except FileNotFoundError:
            logger.error("main_config.toml 未找到。禁用插件列表将初始化为空。")
            # self.excluded_plugins 已经是 []
        except tomllib.TOMLDecodeError as e_toml:
            logger.error(
                f"解析 main_config.toml 时发生错误: {e_toml}。禁用插件列表将初始化为空。"
            )
            # self.excluded_plugins 已经是 []
        except Exception as e_init:
            # 捕获初始化过程中其他可能的异常
            logger.error(
                f"初始化 PluginManager 时读取或解析配置发生未知错误: {e_init}。禁用插件列表将初始化为空。"
            )
            # self.excluded_plugins 已经是 []

    async def load_plugin(
        self,
        bot: WechatAPIClient,
        plugin_class: Type[PluginBase],
        is_disabled: bool = False,
    ) -> bool:
        """加载单个插件，接受Type[PluginBase]"""
        try:
            plugin_name = plugin_class.__name__

            # 防止重复加载插件
            if plugin_name in self.plugins:
                return False

            # 记录插件信息，即使插件被禁用也会记录
            self.plugin_info[plugin_name] = {
                "name": plugin_name,
                "description": plugin_class.description,
                "author": plugin_class.author,
                "version": plugin_class.version,
                "enabled": False,
                "class": plugin_class,
                "is_ai_platform": getattr(
                    plugin_class, "is_ai_platform", False
                ),  # 检查是否为AI平台插件
                "priority": getattr(plugin_class, "priority", 50),  # 记录插件优先级
                "has_global_priority": getattr(
                    plugin_class, "has_global_priority", False
                ),  # 记录是否设置了全局优先级
            }

            # 如果插件被禁用则不加载
            if is_disabled:
                return False

            plugin = plugin_class()
            # 记录插件优先级信息
            priority = getattr(plugin, "priority", 50)
            has_global_priority = getattr(plugin, "has_global_priority", False)

            if has_global_priority:
                logger.info(f"加载插件 {plugin_name}，使用全局优先级: {priority}")
            else:
                logger.info(f"加载插件 {plugin_name}，使用装饰器优先级")

            EventManager.bind_instance(plugin)
            await plugin.on_enable(bot)
            await plugin.async_init()
            self.plugins[plugin_name] = plugin
            self.plugin_classes[plugin_name] = plugin_class
            self.plugin_info[plugin_name]["enabled"] = True
            self.plugin_info[plugin_name]["priority"] = priority  # 更新优先级信息
            self.plugin_info[plugin_name][
                "has_global_priority"
            ] = has_global_priority  # 更新全局优先级标志
            return True
        except:
            logger.error(f"加载插件时发生错误: {traceback.format_exc()}")
            return False

    async def unload_plugin(
        self, plugin_name: str, add_to_excluded: bool = False
    ) -> bool:
        """卸载单个插件

        Args:
            plugin_name: 插件名称
            add_to_excluded: 是否将插件添加到禁用列表中，默认为 False
                          只有在用户主动禁用插件时才应该设置为 True
        """
        if plugin_name not in self.plugins:
            return False

        # 防止卸载 ManagePlugin
        if plugin_name == "ManagePlugin":
            logger.warning("ManagePlugin 不能被卸载")
            return False

        try:
            plugin = self.plugins[plugin_name]
            await plugin.on_disable()
            EventManager.unbind_instance(plugin)
            del self.plugins[plugin_name]
            del self.plugin_classes[plugin_name]
            if plugin_name in self.plugin_info.keys():
                self.plugin_info[plugin_name]["enabled"] = False

            # 只有在用户主动禁用插件时，才将插件添加到禁用列表中
            if add_to_excluded and plugin_name not in self.excluded_plugins:
                self.excluded_plugins.append(plugin_name)
                # 保存禁用插件列表到配置文件
                self._save_disabled_plugins_to_config()
                logger.info(f"将插件 {plugin_name} 添加到禁用列表并保存到配置文件")

            return True
        except:
            logger.error(f"卸载插件 {plugin_name} 时发生错误: {traceback.format_exc()}")
            return False

    async def load_plugins_from_directory(
        self, bot: WechatAPIClient, load_disabled_plugin: bool = True
    ) -> List[str]:
        """从plugins目录批量加载插件"""
        loaded_plugins = []
        failed_plugins = []

        for dirname in os.listdir("plugins"):
            if os.path.isdir(f"plugins/{dirname}") and os.path.exists(
                f"plugins/{dirname}/main.py"
            ):
                try:
                    module = importlib.import_module(f"plugins.{dirname}.main")
                    for name, obj in inspect.getmembers(module):
                        if (
                            inspect.isclass(obj)
                            and issubclass(obj, PluginBase)
                            and obj != PluginBase
                        ):
                            is_disabled = False
                            if not load_disabled_plugin:
                                is_disabled = obj.__name__ in self.excluded_plugins

                            if await self.load_plugin(
                                bot, obj, is_disabled=is_disabled
                            ):
                                loaded_plugins.append(obj.__name__)

                except Exception as e:
                    logger.error(f"加载 {dirname} 时发生错误: {traceback.format_exc()}")
                    failed_plugins.append(dirname)
                    # 继续加载其他插件而不是返回False
                    continue

        if failed_plugins:
            logger.warning(
                f"以下插件加载失败: {', '.join(failed_plugins)}，但不影响其他插件的加载"
            )

        return loaded_plugins

    async def load_plugin_from_directory(
        self, bot: WechatAPIClient, plugin_name: str
    ) -> bool:
        """从plugins目录加载单个插件

        Args:
            bot: 机器人实例
            plugin_name: 插件类名称（不是文件名）

        Returns:
            bool: 是否成功加载插件
        """
        found = False
        for dirname in os.listdir("plugins"):
            try:
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(
                    f"plugins/{dirname}/main.py"
                ):
                    module = importlib.import_module(f"plugins.{dirname}.main")
                    importlib.reload(module)

                    for name, obj in inspect.getmembers(module):
                        if (
                            inspect.isclass(obj)
                            and issubclass(obj, PluginBase)
                            and obj != PluginBase
                            and obj.__name__ == plugin_name
                        ):
                            found = True

                            # 检查是否为AI平台插件
                            is_ai_platform = getattr(obj, "is_ai_platform", False)

                            # 如果是AI平台插件，先禁用其他所有AI平台插件
                            if is_ai_platform:
                                logger.info(
                                    f"启用AI平台插件 {plugin_name}，将禁用其他AI平台插件"
                                )

                                # 遍历已启用的插件，禁用其他AI平台插件
                                for plugin in list(self.plugins):
                                    if (
                                        getattr(
                                            plugin.__class__, "is_ai_platform", False
                                        )
                                        and plugin.__class__.__name__ != plugin_name
                                    ):
                                        logger.info(
                                            f"禁用AI平台插件: {plugin.__class__.__name__}"
                                        )
                                        await self.unload_plugin(
                                            plugin.__class__.__name__
                                        )

                            # 如果插件在禁用列表中，将其移除
                            if plugin_name in self.excluded_plugins:
                                self.excluded_plugins.remove(plugin_name)
                                # 保存禁用插件列表到配置文件
                                self._save_disabled_plugins_to_config()
                                logger.info(
                                    f"将插件 {plugin_name} 从禁用列表中移除并保存到配置文件"
                                )

                            return await self.load_plugin(bot, obj)
            except:
                logger.error(f"检查 {dirname} 时发生错误: {traceback.format_exc()}")
                continue

        if not found:
            logger.warning(f"未找到插件类 {plugin_name}")
            return False

    async def unload_all_plugins(self) -> tuple[List[str], List[str]]:
        """卸载所有插件"""
        unloaded_plugins = []
        failed_unloads = []
        for plugin_name in list(self.plugins.keys()):
            if await self.unload_plugin(plugin_name):
                unloaded_plugins.append(plugin_name)
            else:
                failed_unloads.append(plugin_name)
        return unloaded_plugins, failed_unloads

    async def reload_plugin(self, bot: WechatAPIClient, plugin_name: str) -> bool:
        """重载单个插件"""
        if plugin_name not in self.plugin_classes:
            return False

        # 防止重载 ManagePlugin
        if plugin_name == "ManagePlugin":
            logger.warning("ManagePlugin 不能被重载")
            return False

        try:
            # 获取插件类所在的模块
            plugin_class = self.plugin_classes[plugin_name]
            module_name = plugin_class.__module__

            # 先卸载插件
            if not await self.unload_plugin(plugin_name):
                return False

            # 重新导入模块
            module = importlib.import_module(module_name)
            importlib.reload(module)

            # 从重新加载的模块中获取插件类
            for name, obj in inspect.getmembers(module):
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, PluginBase)
                    and obj != PluginBase
                    and obj.__name__ == plugin_name
                ):
                    # 使用新的插件类而不是旧的
                    return await self.load_plugin(bot, obj)

            return False
        except Exception as e:
            logger.error(f"重载插件 {plugin_name} 时发生错误: {e}")
            return False

    async def reload_all_plugins(
        self, bot: WechatAPIClient
    ) -> tuple[List[str], List[str]]:
        """重载所有插件

        Returns:
            tuple[List[str], List[str]]: 成功重载的插件名称列表和失败的插件名称列表
        """
        try:
            # 记录当前加载的插件名称，排除 ManagePlugin
            original_plugins = [
                name for name in self.plugins.keys() if name != "ManagePlugin"
            ]

            # 我们不在这里更新禁用插件列表
            # 因为这会导致所有当前未启用的插件都被添加到禁用列表中
            # 包括那些只是暂时未加载的插件
            # 我们只在 unload_plugin 方法中更新禁用列表，即用户主动禁用插件时

            # 卸载除 ManagePlugin 外的所有插件
            # 注意这里不将插件添加到禁用列表中，因为这只是重载而非禁用
            for plugin_name in original_plugins:
                await self.unload_plugin(plugin_name, add_to_excluded=False)

            # 重新加载所有模块
            for module_name in list(sys.modules.keys()):
                if module_name.startswith("plugins.") and not module_name.endswith(
                    "ManagePlugin"
                ):
                    del sys.modules[module_name]

            # 从目录重新加载插件，不加载禁用的插件
            loaded_plugins = []
            failed_plugins = []

            # 从plugins目录批量加载插件
            for dirname in os.listdir("plugins"):
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(
                    f"plugins/{dirname}/main.py"
                ):
                    try:
                        module = importlib.import_module(f"plugins.{dirname}.main")
                        for name, obj in inspect.getmembers(module):
                            if (
                                inspect.isclass(obj)
                                and issubclass(obj, PluginBase)
                                and obj != PluginBase
                            ):
                                is_disabled = obj.__name__ in self.excluded_plugins

                                if not is_disabled and await self.load_plugin(
                                    bot, obj, is_disabled=is_disabled
                                ):
                                    loaded_plugins.append(obj.__name__)
                    except Exception as e:
                        logger.error(
                            f"重载插件 {dirname} 时发生错误: {traceback.format_exc()}"
                        )
                        failed_plugins.append(dirname)
                        continue

            if failed_plugins:
                logger.warning(
                    f"以下插件重载失败: {', '.join(failed_plugins)}，但不影响其他插件的加载"
                )

            return loaded_plugins, failed_plugins

        except:
            logger.error(f"重载所有插件时发生错误: {traceback.format_exc()}")
            return [], []

    def get_plugin_info(self, plugin_name: str = None) -> Union[dict, List[dict]]:
        """获取插件信息

        Args:
            plugin_name: 插件名称，如果为None则返回所有插件信息

        Returns:
            如果指定插件名，返回单个插件信息字典；否则返回所有插件信息列表
        """

        # 创建一个可以JSON序列化的信息副本
        def clean_plugin_info(info):
            plugin_name = info.get("name", "unknown")
            has_global_priority = info.get("has_global_priority", False)

            # 获取插件的装饰器优先级
            decorator_priority = 50  # 默认值

            # 如果没有设置全局优先级，获取装饰器优先级
            if not has_global_priority:
                from utils.event_manager import EventManager

                method_priorities = EventManager.get_method_priorities(plugin_name)

                # 如果有方法优先级记录，则使用
                if method_priorities:
                    # 记录所有方法的优先级，用于调试
                    from loguru import logger

                    logger.debug(
                        f"插件 {plugin_name} 的方法优先级: {method_priorities}"
                    )

                    # 找出最高优先级的方法
                    if method_priorities:
                        # 提取所有方法的优先级值
                        priorities = [
                            method_info["priority"]
                            for method_info in method_priorities.values()
                        ]
                        if priorities:
                            max_priority = max(priorities)
                            decorator_priority = max_priority

                            # 记录使用的优先级
                            logger.debug(
                                f"插件 {plugin_name} 使用最高装饰器优先级: {decorator_priority}"
                            )

                # 如果插件未加载或没有方法优先级记录，尝试从插件类中获取
                elif plugin_name in self.plugin_classes:
                    # 获取插件类
                    plugin_class = self.plugin_classes[plugin_name]

                    # 检查插件类中的方法，查找装饰器优先级
                    from loguru import logger

                    max_priority = 50  # 默认优先级

                    for method_name in dir(plugin_class):
                        if method_name.startswith("__"):
                            continue

                        method = getattr(plugin_class, method_name)
                        if hasattr(method, "_priority"):
                            priority = getattr(method, "_priority", 50)
                            if priority > max_priority:
                                max_priority = priority

                    decorator_priority = max_priority
                    logger.debug(
                        f"插件 {plugin_name} 从类定义中获取最高装饰器优先级: {decorator_priority}"
                    )

                # 如果插件未加载且没有类定义，尝试从插件信息中获取
                elif "class" in info:
                    plugin_class = info["class"]

                    # 检查插件类中的方法，查找装饰器优先级
                    from loguru import logger

                    max_priority = 50  # 默认优先级

                    for method_name in dir(plugin_class):
                        if method_name.startswith("__"):
                            continue

                        method = getattr(plugin_class, method_name)
                        if hasattr(method, "_priority"):
                            priority = getattr(method, "_priority", 50)
                            if priority > max_priority:
                                max_priority = priority

                    decorator_priority = max_priority
                    logger.debug(
                        f"插件 {plugin_name} 从插件信息中获取最高装饰器优先级: {decorator_priority}"
                    )

            # 确定最终优先级
            final_priority = decorator_priority
            if has_global_priority:
                final_priority = info.get("priority", 50)

            result = {
                "id": plugin_name,
                "name": plugin_name,
                "description": info.get("description", ""),
                "author": info.get("author", ""),
                "version": info.get("version", "1.0.0"),
                "enabled": info.get("enabled", False),
                "is_ai_platform": info.get("is_ai_platform", False),  # 添加AI平台标识
                "priority": final_priority,  # 使用全局优先级或装饰器最高优先级
                "has_global_priority": has_global_priority,  # 添加全局优先级标志
            }
            return result

        if plugin_name:
            info = self.plugin_info.get(plugin_name)
            if info:
                return clean_plugin_info(info)
            return None

        return [clean_plugin_info(info) for info in self.plugin_info.values()]

    def _save_disabled_plugins_to_config(self):
        """将禁用的插件列表保存到配置文件中"""
        try:
            import tomli_w

            # 读取当前配置
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)

            # 更新禁用插件列表
            config["XYBot"]["disabled-plugins"] = self.excluded_plugins

            # 写回配置文件
            with open("main_config.toml", "wb") as f:
                tomli_w.dump(config, f)

            logger.info(f"成功将禁用插件列表保存到配置文件: {self.excluded_plugins}")
        except Exception as e:
            logger.error(f"保存禁用插件列表到配置文件失败: {e}")

    def get_ai_platform_plugins(self) -> List[dict]:
        """获取所有AI平台插件信息"""
        # 使用与 get_plugin_info 相同的格式化方法，但不重复实现
        # 直接调用 get_plugin_info 方法获取所有插件，然后过滤出AI平台插件
        all_plugins = self.get_plugin_info()
        return [plugin for plugin in all_plugins if plugin.get("is_ai_platform", False)]


plugin_manager = PluginManager()
