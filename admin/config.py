"""
管理后台配置文件 - 从main_config.toml读取统一配置
"""
import os
import sys
import tomllib
from pathlib import Path

# 版本信息
VERSION = "1.0.0"

# 从main_config.toml读取配置
def load_config_from_toml():
    # 获取main_config.toml的路径
    main_dir = Path(__file__).resolve().parent.parent
    config_path = main_dir / "main_config.toml"
    
    # 默认配置，如果读取失败会使用这些值
    default_admin_config = {
        "host": "0.0.0.0",
        "port": 9090,
        "username": "admin",
        "password": "admin123",
        "debug": False,
        "secret_key": "admin_secret_key", 
        "max_history": 1000
    }
    
    try:
        if config_path.exists():
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                
            # 从配置中读取Admin部分
            admin_config = config.get("Admin", {})
            
            # 合并默认配置和从toml读取的配置
            ADMIN_CONFIG = default_admin_config.copy()
            for key in default_admin_config:
                if key in admin_config:
                    ADMIN_CONFIG[key] = admin_config[key]
                    
            return ADMIN_CONFIG
        else:
            print(f"警告: 未找到配置文件 {config_path}，使用默认配置")
            return default_admin_config
    except Exception as e:
        print(f"加载配置文件出错: {e}，使用默认配置")
        return default_admin_config

# 管理后台配置
ADMIN_CONFIG = load_config_from_toml()

# API配置
API_CONFIG = {
    "timeout": 30,
    "retry": 3,
    "cache_ttl": 3600
}

# 日志配置
LOG_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s | %(levelname)s | %(message)s",
    "date_format": "%Y-%m-%d %H:%M:%S"
}

# 插件市场配置
PLUGIN_MARKET_CONFIG = {
    "base_url": os.environ.get("PLUGIN_MARKET_BASE_URL", "http://v.sxkiss.top"),
    "cache_dir": "_cache",
    "temp_dir": "_temp",
    "sync_interval": 3600
}
 
