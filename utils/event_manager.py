import copy
from typing import Callable, Dict, List


class EventManager:
    _handlers: Dict[str, List[tuple[Callable, object, int]]] = {}
    _method_priorities: Dict[str, Dict[str, int]] = {}  # 存储每个插件方法的原始优先级

    @classmethod
    def bind_instance(cls, instance: object):
        """将实例绑定到对应的事件处理函数"""
        # 检查插件是否设置了全局优先级
        has_global_priority = getattr(instance, "has_global_priority", False)
        global_priority = getattr(instance, "priority", 50)

        # 收集插件中所有方法的原始优先级
        plugin_name = instance.__class__.__name__
        method_priorities = {}

        for method_name in dir(instance):
            method = getattr(instance, method_name)
            if hasattr(method, "_event_type"):
                event_type = getattr(method, "_event_type")
                # 获取方法的优先级，如果没有则使用默认值50
                method_priority = getattr(method, "_priority", 50)

                # 记录方法的原始优先级和事件类型
                method_priorities[method_name] = {
                    "priority": method_priority,
                    "event_type": event_type,
                }

                # 计算最终优先级：
                # 如果设置了全局优先级，则使用全局优先级
                # 否则使用方法自己的优先级
                if has_global_priority:
                    final_priority = global_priority
                    # 记录优先级变更日志
                    if method_priority != global_priority:
                        from loguru import logger

                        logger.debug(
                            f"插件 {plugin_name} 的方法 {method_name} 优先级从 {method_priority} 调整为全局优先级 {global_priority}"
                        )
                else:
                    final_priority = method_priority
                    from loguru import logger

                    logger.debug(
                        f"插件 {plugin_name} 的方法 {method_name} 使用装饰器优先级: {method_priority}"
                    )

                if event_type not in cls._handlers:
                    cls._handlers[event_type] = []
                cls._handlers[event_type].append((method, instance, final_priority))
                # 按优先级排序，优先级高的在前
                cls._handlers[event_type].sort(key=lambda x: x[2], reverse=True)

        # 存储插件的方法优先级
        cls._method_priorities[plugin_name] = method_priorities

    @classmethod
    async def emit(cls, event_type: str, *args, **kwargs):
        """触发事件

        Args:
            event_type: 事件类型
            *args: 位置参数
            **kwargs: 关键字参数，可以包含 callback 回调函数

        Returns:
            如果有处理函数返回 False，则返回 False；否则返回 None
        """
        # 提取 callback 参数，如果没有则为 None
        callback = kwargs.pop("callback", None)

        if event_type not in cls._handlers:
            # 如果有回调函数，调用它并传递 None
            if callback:
                callback(None)
            return None

        api_client, message = args
        final_result = None

        for handler, instance, priority in cls._handlers[event_type]:
            # 只对 message 进行深拷贝，api_client 保持不变
            handler_args = (api_client, copy.deepcopy(message))
            new_kwargs = {k: copy.deepcopy(v) for k, v in kwargs.items()}

            result = await handler(*handler_args, **new_kwargs)

            # 记录最后一个非 None 的结果
            if result is not None:
                final_result = result

            if isinstance(result, bool):
                # True 继续执行 False 停止执行
                if not result:
                    # 如果有回调函数，调用它并传递结果
                    if callback:
                        callback(False)
                    return False
            else:
                continue  # 我也不知道你返回了个啥玩意，反正继续执行就是了

        # 如果有回调函数，调用它并传递最终结果
        if callback:
            callback(final_result)

        return final_result

    @classmethod
    def unbind_instance(cls, instance: object):
        """解绑实例的所有事件处理函数"""
        # 获取插件名称
        plugin_name = instance.__class__.__name__

        # 从方法优先级字典中移除该插件
        if plugin_name in cls._method_priorities:
            del cls._method_priorities[plugin_name]

        for event_type in cls._handlers:
            cls._handlers[event_type] = [
                (handler, inst, priority)
                for handler, inst, priority in cls._handlers[event_type]
                if inst is not instance
            ]

    @classmethod
    def get_method_priorities(cls, plugin_name: str) -> Dict[str, int]:
        """获取插件方法的原始优先级

        Args:
            plugin_name: 插件名称

        Returns:
            包含方法名和优先级的字典
        """
        return cls._method_priorities.get(plugin_name, {})
