"""
插件管理器模拟类，用于在admin模块中处理插件相关操作
"""
import logging
import os
import sys

# 设置日志
logger = logging.getLogger("admin_plugin_manager")

class PluginManager:
    """
    插件管理器类，提供获取插件信息、加载和卸载插件的功能
    """
    def __init__(self):
        """初始化插件管理器"""
        self.plugins = {}
        self.plugin_info = {}
        self.excluded_plugins = []

    def get_plugin_info(self):
        """
        获取插件信息列表
        
        Returns:
            list: 插件信息列表
        """
        try:
            # 尝试从主程序导入插件信息
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            
            try:
                # 尝试从主程序中导入插件管理器
                from utils.plugin_manager import plugin_manager as main_plugin_manager
                # 从主程序获取插件列表
                plugins_info = main_plugin_manager.get_plugin_info()
                return plugins_info
            except ImportError:
                logger.warning("无法导入主程序的插件管理器，返回空列表")
                return []
            except Exception as e:
                logger.error(f"获取主程序插件信息失败: {e}")
                return []
        except Exception as e:
            logger.error(f"导入路径设置失败: {e}")
            return []

    async def load_plugin_from_directory(self, bot, plugin_id):
        """
        从目录加载插件（模拟方法）
        
        Args:
            bot: 机器人实例
            plugin_id: 插件ID
            
        Returns:
            bool: 是否成功
        """
        # 这里仅作为模拟，实际操作会在主程序中执行
        return True
    
    async def unload_plugin(self, plugin_id, add_to_excluded=False):
        """
        卸载插件（模拟方法）
        
        Args:
            plugin_id: 插件ID
            add_to_excluded: 是否添加到排除列表
            
        Returns:
            bool: 是否成功
        """
        # 这里仅作为模拟，实际操作会在主程序中执行
        if add_to_excluded and plugin_id not in self.excluded_plugins:
            self.excluded_plugins.append(plugin_id)
        return True

# 创建插件管理器实例
plugin_manager = PluginManager() 