import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from utils.singleton import Singleton

class MessageCounter(metaclass=Singleton):
    """消息计数器，用于统计机器人处理的消息数量"""
    
    def __init__(self):
        """初始化消息计数器"""
        self.stats_file = Path("message_stats.json")
        self.total_messages = 0
        self.daily_messages = {}  # 按日期存储 {"YYYY-MM-DD": count}
        self.platform_messages = {}  # 按平台分类 {"platform_name": count}
        self.last_save = time.time()
        self.save_interval = 60  # 60秒保存一次，避免频繁IO
        
        # 加载现有数据
        self._load_stats()
        logger.success("消息计数器初始化成功")
    
    def _load_stats(self):
        """从文件加载统计数据"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
                
                self.total_messages = stats.get("total_messages", 0)
                self.daily_messages = stats.get("daily_messages", {})
                self.platform_messages = stats.get("platform_messages", {})
                logger.info(f"已加载消息统计数据，总消息数：{self.total_messages}")
            except Exception as e:
                logger.error(f"加载消息统计数据失败: {e}")
        else:
            logger.info("未找到消息统计数据，将创建新的统计记录")
    
    def _save_stats(self):
        """保存统计数据到文件"""
        try:
            stats = {
                "total_messages": self.total_messages,
                "daily_messages": self.daily_messages,
                "platform_messages": self.platform_messages,
                "last_update": datetime.now().isoformat()
            }
            
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
                
            self.last_save = time.time()
            logger.debug(f"已保存消息统计数据，总消息数：{self.total_messages}")
        except Exception as e:
            logger.error(f"保存消息统计数据失败: {e}")
    
    def count_message(self, platform="wechat"):
        """统计一条消息"""
        # 增加总消息计数
        self.total_messages += 1
        
        # 按日期统计
        today = datetime.now().strftime("%Y-%m-%d")
        self.daily_messages[today] = self.daily_messages.get(today, 0) + 1
        
        # 按平台统计
        self.platform_messages[platform] = self.platform_messages.get(platform, 0) + 1
        
        # 定期保存数据
        if time.time() - self.last_save > self.save_interval:
            self._save_stats()
    
    def get_today_messages(self):
        """获取今日消息数"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.daily_messages.get(today, 0)
    
    def get_platform_count(self):
        """获取平台数量"""
        return len(self.platform_messages)
    
    def get_stats(self):
        """获取统计数据"""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - 
                    timedelta(days=1)).strftime("%Y-%m-%d")
        
        growth_rate = 0
        yesterday_count = self.daily_messages.get(yesterday, 0)
        today_count = self.daily_messages.get(today, 0)
        
        if yesterday_count > 0:
            growth_rate = ((today_count - yesterday_count) / yesterday_count) * 100
        elif today_count > 0 and yesterday_count == 0:  # 今天有消息但昨天没有
            growth_rate = 100  # 设置为100%增长率
        
        # 计算平均每日消息数
        if len(self.daily_messages) > 0:
            avg_messages = self.total_messages / len(self.daily_messages)
        else:
            avg_messages = 0
        
        return {
            "total_messages": self.total_messages,
            "today_messages": today_count,
            "avg_daily": round(avg_messages, 2),  # 四舍五入到2位小数
            "growth_rate": round(growth_rate, 1),  # 四舍五入到1位小数
            "platform_count": len(self.platform_messages),
            "platforms": self.platform_messages
        }
        
    def get_recent_stats(self, days=7):
        """获取最近几天的消息统计
        
        Args:
            days: 获取最近多少天的数据
            
        Returns:
            dict: 包含最近几天每天的消息数量
        """
        result = {}
        today = datetime.now().date()
        
        # 获取最近days天的数据
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            result[date_str] = self.daily_messages.get(date_str, 0)
        
        return result