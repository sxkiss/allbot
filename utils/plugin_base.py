import os
import sys
from abc import ABC

from loguru import logger

from .decorators import add_job_safe, remove_job_safe, scheduler


class PluginBase(ABC):
    """插件基类"""

    # 插件元数据
    description: str = "暂无描述"
    author: str = "未知"
    version: str = "1.0.0"
    is_ai_platform: bool = False  # 标记是否为AI平台插件

    # 标记是否设置了全局优先级
    has_global_priority: bool = False
    # 默认全局优先级值
    priority: int = 50

    def __init__(self):
        self.enabled = False
        self._scheduled_jobs = set()

        # 尝试从配置文件中读取全局优先级
        try:
            import os
            import tomllib

            # 获取插件目录路径
            plugin_dir = os.path.dirname(
                sys.modules[self.__class__.__module__].__file__
            )
            config_path = os.path.join(plugin_dir, "config.toml")

            if os.path.exists(config_path):
                with open(config_path, "rb") as f:
                    config = tomllib.load(f)

                # 尝试从配置文件中读取优先级
                # 首先尝试从basic部分读取
                basic_config = config.get("basic", {})
                if "priority" in basic_config:
                    self.priority = min(max(int(basic_config["priority"]), 0), 99)
                    self.has_global_priority = True
                    logger.debug(
                        f"从[basic]部分读取到插件 {self.__class__.__name__} 的全局优先级: {self.priority}"
                    )

                # 如果basic部分没有，尝试从插件名称部分读取
                elif (
                    self.__class__.__name__ in config
                    and "priority" in config[self.__class__.__name__]
                ):
                    self.priority = min(
                        max(int(config[self.__class__.__name__]["priority"]), 0), 99
                    )
                    self.has_global_priority = True
                    logger.debug(
                        f"从[{self.__class__.__name__}]部分读取到插件 {self.__class__.__name__} 的全局优先级: {self.priority}"
                    )

                # 如果都没有，不设置全局优先级标志，使用装饰器中的优先级
                else:
                    logger.debug(
                        f"未在配置文件中找到插件 {self.__class__.__name__} 的全局优先级，将使用装饰器中的优先级"
                    )
        except Exception as e:
            logger.warning(
                f"读取插件 {self.__class__.__name__} 的全局优先级时出错: {str(e)}"
            )
            # 出错时不设置全局优先级标志

    async def on_enable(self, bot=None):
        """插件启用时调用"""

        # 定时任务
        for method_name in dir(self):
            method = getattr(self, method_name)
            if hasattr(method, "_is_scheduled"):
                job_id = getattr(method, "_job_id")
                trigger = getattr(method, "_schedule_trigger")
                trigger_args = getattr(method, "_schedule_args")

                add_job_safe(scheduler, job_id, method, bot, trigger, **trigger_args)
                self._scheduled_jobs.add(job_id)
        if self._scheduled_jobs:
            logger.success(
                "插件 {} 已加载定时任务: {}",
                self.__class__.__name__,
                self._scheduled_jobs,
            )

    async def on_disable(self):
        """插件禁用时调用"""

        # 移除定时任务
        for job_id in self._scheduled_jobs:
            remove_job_safe(scheduler, job_id)
        logger.info("已卸载定时任务: {}", self._scheduled_jobs)
        self._scheduled_jobs.clear()

    async def async_init(self):
        """插件异步初始化"""
        return
