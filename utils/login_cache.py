"""
登录缓存管理模块
用于优化登录流程，支持缓存登录和二次登录
"""
import json
import os
from pathlib import Path
from typing import Optional, Dict
from loguru import logger


class LoginCache:
    """登录缓存管理器"""
    
    def __init__(self, cache_dir: str = "resource"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.robot_stat_file = self.cache_dir / "robot_stat.json"
        self.login_stat_file = Path("WechatAPI/Client/login_stat.json")
    
    def load_robot_stat(self) -> Dict:
        """加载机器人状态信息"""
        if not self.robot_stat_file.exists():
            default_stat = {"wxid": "", "device_name": "", "device_id": ""}
            self.save_robot_stat(default_stat)
            return default_stat
        
        try:
            with open(self.robot_stat_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取robot_stat.json失败: {e}")
            return {"wxid": "", "device_name": "", "device_id": ""}
    
    def save_robot_stat(self, stat: Dict) -> bool:
        """保存机器人状态信息"""
        try:
            with open(self.robot_stat_file, "w", encoding="utf-8") as f:
                json.dump(stat, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存robot_stat.json失败: {e}")
            return False
    
    def update_login_info(self, wxid: str, device_name: str = "", device_id: str = "") -> bool:
        """更新登录信息"""
        stat = self.load_robot_stat()
        stat["wxid"] = wxid
        if device_name:
            stat["device_name"] = device_name
        if device_id:
            stat["device_id"] = device_id
        return self.save_robot_stat(stat)
    
    def get_wxid(self) -> Optional[str]:
        """获取缓存的wxid"""
        stat = self.load_robot_stat()
        wxid = stat.get("wxid", "")
        return wxid if wxid else None
    
    def get_device_info(self) -> tuple[Optional[str], Optional[str]]:
        """获取设备信息"""
        stat = self.load_robot_stat()
        device_name = stat.get("device_name", "")
        device_id = stat.get("device_id", "")
        return (device_name if device_name else None, 
                device_id if device_id else None)
    
    def clear_cache(self) -> bool:
        """清除登录缓存"""
        try:
            if self.robot_stat_file.exists():
                os.remove(self.robot_stat_file)
            if self.login_stat_file.exists():
                os.remove(self.login_stat_file)
            logger.info("登录缓存已清除")
            return True
        except Exception as e:
            logger.error(f"清除登录缓存失败: {e}")
            return False