"""
@input: WechatAPI bot 客户端、httpx/ffmpeg/cv2 等媒体依赖、插件配置与本地临时视频文件
@output: VideoDemand 插件，处理分类视频、随机视频与 URL 视频请求并通过桥接 bot 接口回发
@position: allbot-openclaw 桥接内的视频点播插件副本；下载完成后优先发送本地视频路径，避免超长 base64 视频挤占网关链路
@auto-doc: Update header and folder INDEX.md when this file changes
"""

import json
import re
import tomllib
import traceback
import asyncio
import os
import time
import base64
from pathlib import Path
import httpx
from loguru import logger
from typing import Optional
import subprocess
import binascii
import shutil
import random

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase

class VideoDemand(PluginBase):
    """视频点播插件"""

    description = "视频点播插件 - 支持多类型视频点播"
    author = "AllBot"
    version = "1.0.0"

    def __init__(self):
        super().__init__()

        # 初始化临时目录 - 使用绝对路径
        self.plugin_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        self.temp_dir = self.plugin_dir / "temp"
        self._ensure_temp_dir()

        # 读取配置
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.toml")
            with open(config_path, "rb") as f:
                plugin_config = tomllib.load(f)
            config = plugin_config["VideoDemand"]
        except Exception as e:
            logger.error(f"[VideoDemand] 配置文件读取失败: {e}")
            raise

        # 基本配置
        self.enable = config.get("enable", True)
        self.command = config.get("command", ["视频菜单"])
        self.random_command = config.get("random-command", ["随机视频"])  # 添加随机视频命令
        self.random_video_url = config.get("random-video-url", "https://sj.tuituiya.cn/sjdsp/zsxjj669.php")  # 随机视频URL
        self.menu_image = config.get("menu-image", "https://d.kstore.dev/download/8150/shipin.jpg")
        self.cache_time = config.get("cache-time", 300)  # 菜单有效期5分钟

        # 房间状态
        self.room_status = {}

        # 并发控制
        self.video_semaphore = asyncio.Semaphore(3)  # 最多同时处理3个视频请求

        # 磁盘空间检查阈值（字节）
        self.min_disk_space = 1024 * 1024 * 1024  # 1GB

        # API配置
        self.api_mapping = {
            1: {'name': '热舞视频', 'urls': [
                'http://api.yujn.cn/api/rewu.php?type=video',
                'https://api.317ak.com/API/sp/rwxl.php'
            ]},
            2: {'name': '吧啦鲨系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=吧啦鲨系',
                'https://api.317ak.com/API/sp/blsx.php'
            ]},
            3: {'name': '丁璐系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=丁璐系列',
                'https://api.317ak.com/API/sp/dlxl.php'
            ]},
            4: {'name': '不怪系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=不怪系列',
                'https://api.317ak.com/API/sp/bgxl.php'
            ]},
            5: {'name': '不是小媛', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=不是小媛',
                'https://api.317ak.com/API/sp/bsxy.php'
            ]},
            6: {'name': '不见花海', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=不见花海',
                'https://api.317ak.com/API/sp/bjhh.php'
            ]},
            7: {'name': '二爷系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=二爷系列',
                'https://api.317ak.com/API/sp/eyxl.php'
            ]},
            8: {'name': '二酱系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=二酱系列',
                'https://api.317ak.com/API/sp/ejxl.php'
            ]},
            9: {'name': '二麻翻唱', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=二麻翻唱',
                'https://api.317ak.com/API/sp/emfc.php'
            ]},
            10: {'name': '健身系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=健身系列',
                'https://api.317ak.com/API/sp/jsxl.php'
            ]},
            11: {'name': '傲娇媛系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=傲娇媛系'
            ]},
            12: {'name': '凌凌七系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=凌凌七系'
            ]},
            13: {'name': '半斤系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=半斤系列'
            ]},
            14: {'name': '半糖糖系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=半糖糖系'
            ]},
            15: {'name': '卿卿公主', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=卿卿公主'
            ]},
            16: {'name': '呆萝系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=呆萝系列'
            ]},
            17: {'name': '妲己系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=妲己系列'
            ]},
            18: {'name': '安佳系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=安佳系列'
            ]},
            19: {'name': '安然系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=安然系列'
            ]},
            20: {'name': '宋熙雅系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=宋熙雅系'
            ]},
            21: {'name': '富儿系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=富儿系列'
            ]},
            22: {'name': '小苏伊伊', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=小苏伊伊'
            ]},
            23: {'name': '小落英系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=小落英系'
            ]},
            24: {'name': '巴啦魔仙', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=巴啦魔仙'
            ]},
            25: {'name': '暴力美系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=暴力美系'
            ]},
            26: {'name': '梦瑶系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=梦瑶系列'
            ]},
            27: {'name': '江小系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=江小系列'
            ]},
            28: {'name': '江青系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=江青系列'
            ]},
            29: {'name': '海绵翻唱', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=海绵翻唱'
            ]},
            30: {'name': '涵涵系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=涵涵系列'
            ]},
            31: {'name': '温柔以待', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=温柔以待'
            ]},
            32: {'name': '爆笑阿衰', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=爆笑阿衰'
            ]},
            33: {'name': '爱希系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=爱希系列'
            ]},
            34: {'name': '白月光系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=白月光系'
            ]},
            35: {'name': '白璃系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=白璃系列'
            ]},
            36: {'name': '白露系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=白露系列'
            ]},
            37: {'name': '百变小晨', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=百变小晨'
            ]},
            38: {'name': '等等系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=等等系列'
            ]},
            39: {'name': '糕冷小王', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=糕冷小王'
            ]},
            40: {'name': '红姐系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=红姐系列'
            ]},
            41: {'name': '绷带很烦', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=绷带很烦'
            ]},
            42: {'name': '美杜莎系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=美杜莎系'
            ]},
            43: {'name': '翠翠系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=翠翠系列'
            ]},
            44: {'name': '背影变装', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=背影变装'
            ]},
            45: {'name': '腹肌变装', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=腹肌变装'
            ]},
            46: {'name': '花花姑娘', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=花花姑娘'
            ]},
            47: {'name': '茶茶欧尼', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=茶茶欧尼'
            ]},
            48: {'name': '菜小怡系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=菜小怡系'
            ]},
            49: {'name': '过肩出场', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=过肩出场'
            ]},
            50: {'name': '陈和系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=陈和系列'
            ]},
            51: {'name': '蛋儿系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=蛋儿系列',
                'https://api.317ak.com/API/sp/dexl.php'
            ]},
            52: {'name': '美女视频', 'urls': [
                'https://api.71xk.com/api/video/v1',
                'http://www.yujn.cn/api/xjj.php',
                'http://www.yujn.cn/api/zzxjj.php',
                'http://api.yujn.cn/api/juhexjj.php?type=video',
                'https://api.cenguigui.cn/api/mp4/MP4_xiaojiejie.php',
                'https://api.kuleu.com/api/MP4_xiaojiejie?type=video',
                'https://api.pearktrue.cn/api/random/xjj/?type=video',
                'https://www.wudada.online/Api/NewSp',
                'https://v2.api-m.com/api/meinv?return=302'
            ]},
            53: {'name': '安琪系列', 'urls': [
                'https://api.317ak.com/API/sp/aqxl.php'
            ]},
            54: {'name': '双倍快乐', 'urls': [
                'http://api.yujn.cn/api/sbkl.php?type=video'
            ]},
            55: {'name': '变装系列', 'urls': [
                'https://api.317ak.com/API/sp/gjbz.php',
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=光剑变装',
                'https://api.317ak.com/API/sp/dmbz.php'
            ]},
            56: {'name': '女神系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=女神'
            ]},
            57: {'name': '动漫系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=动漫'
            ]},
            58: {'name': '动物系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=动物'
            ]},
            59: {'name': '风景系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=风景'
            ]},
            60: {'name': '情侣系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=情侣'
            ]},
            61: {'name': '姓氏特效', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=姓氏特效'
            ]},
            62: {'name': '酷炫特效', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=酷炫特效'
            ]},
            63: {'name': '动态壁纸', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=动态壁纸'
            ]},
            64: {'name': '热歌系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=热歌'
            ]},
            65: {'name': '男神系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=男神'
            ]},
            66: {'name': '明星系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=明星'
            ]},
            67: {'name': '节日系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=节日'
            ]},
            68: {'name': '充电系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=充电'
            ]},
            69: {'name': '闹钟系列', 'urls': [
                'https://api.suyanw.cn/api/kysp.php?lx=闹钟'
            ]},
            70: {'name': '萌娃系列', 'urls': [
                'https://api.317ak.com/API/sp/mwxl.php'
            ]},
            71: {'name': '桥本环菜', 'urls': [
                'https://api.317ak.com/API/sp/qbhc.php'
            ]},
            72: {'name': '燕酱系列', 'urls': [
                'https://api.317ak.com/API/sp/yjxl.php'
            ]},
            73: {'name': '自拍视频', 'urls': [
                'https://api.317ak.com/API/sp/zpsp.php'
            ]},
            74: {'name': '双马尾系', 'urls': [
                'https://api.317ak.com/API/sp/smwx.php'
            ]},
            75: {'name': '渔网系列', 'urls': [
                'https://api.317ak.com/API/sp/ywxl.php'
            ]},
            76: {'name': '鞠婧祎系', 'urls': [
                'https://api.317ak.com/API/sp/jjyx.php'
            ]},
            77: {'name': '漫展系列', 'urls': [
                'https://www.yujn.cn/api/manzhan.php'
            ]},
            78: {'name': '周扬青系', 'urls': [
                'https://api.317ak.com/API/sp/zyqx.php'
            ]},
            79: {'name': '周清欢系', 'urls': [
                'https://api.317ak.com/API/sp/xqx.php'
            ]},
            80: {'name': '极品狱卒', 'urls': [
                'https://api.317ak.com/API/sp/jpyz.php'
            ]},
            81: {'name': '纯情女高', 'urls': [
                'https://api.317ak.com/API/sp/cqng.php',
                'https://api.317ak.com/API/sp/ndxl.php'
            ]},
            82: {'name': '漫画芋系', 'urls': [
                'https://api.317ak.com/API/sp/mhyx.php'
            ]},
            83: {'name': '感觉至上', 'urls': [
                'https://api.dragonlongzhu.cn/api/MP4_xiaojiejie.php'
            ]},
            84: {'name': '开心锤锤', 'urls': [
                'http://abc.gykj.asia/API/kxcc.php'
            ]},
            85: {'name': '动漫卡点', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=动漫系列'
            ]},
            86: {'name': '少萝系列', 'urls': [
                'https://api.317ak.com/API/sp/slmm.php',
                'https://api.317ak.com/API/sp/slxl.php'
            ]},
            87: {'name': '甩裙系列', 'urls': [
                'https://api.317ak.com/API/sp/sqxl.php'
            ]},
            88: {'name': '黑白双煞', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=黑白双丝',
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=黑白双煞',
                'https://api.317ak.com/API/sp/hbss.php'
            ]},
            89: {'name': '吊带系列', 'urls': [
                'https://api.317ak.com/API/sp/ddxl.php',
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=吊带系列',
                'https://api.317ak.com/API/sp/ddsp.php'
            ]},
            90: {'name': '萝莉系列', 'urls': [
                'https://api.317ak.com/API/sp/llxl.php'
            ]},
            91: {'name': '甜妹系列', 'urls': [
                'https://api.317ak.com/API/sp/tmxl.php'
            ]},
            92: {'name': '白丝系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=白丝系列',
                'https://api.317ak.com/API/sp/bssp.php'
            ]},
            93: {'name': '黑丝系列', 'urls': [
                'https://api.317ak.com/API/sp/hssp.php',
                'http://www.yujn.cn/api/heisis.php'
            ]},
            94: {'name': '小瑾系列', 'urls': [
                'https://api.317ak.com/API/sp/xjxl.php'
            ]},
            95: {'name': '穿搭系列', 'urls': [
                'http://api.yujn.cn/api/chuanda.php?type=video',
                'https://api.317ak.com/API/sp/cdxl.php',
                'https://api.317ak.com/API/sp/mncd.php'
            ]},
            96: {'name': '惠子系列', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=惠子系列',
                'https://api.317ak.com/API/sp/hzxl.php'
            ]},
            97: {'name': '御姐系列', 'urls': [
                'https://api.317ak.com/API/sp/yjxl.php'
            ]},
            98: {'name': '女仆系列', 'urls': [
                'https://api.317ak.com/API/sp/npxl.php'
            ]},
            99: {'name': '微胖系列', 'urls': [
                'https://api.317ak.com/API/sp/wpxl.php'
            ]},
            100: {'name': '硬气卡点', 'urls': [
                'https://api.317ak.com/API/sp/yqkd.php'
            ]},
            101: {'name': '火车摇系', 'urls': [
                'https://api.317ak.com/API/sp/hcyx.php'
            ]},
            102: {'name': '安慕希系', 'urls': [
                'https://api.317ak.com/API/sp/amxx.php',
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=吊带系列'
            ]},
            103: {'name': '擦玻璃系', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=擦玻璃系',
                'https://api.317ak.com/API/sp/cblx.php'
            ]},
            104: {'name': '蹲下变装', 'urls': [
                'https://api.bi71t5.cn/api/wbsphj.php?xuanze=蹲下变装',
                'https://api.317ak.com/API/sp/dxbz.php'
            ]},
            105: {'name': '背影变装', 'urls': [
                'https://api.317ak.com/API/sp/bybz.php'
            ]},
            106: {'name': '猫系女友', 'urls': [
                'https://api.317ak.com/API/sp/mxny.php'
            ]},
            107: {'name': '丝滑舞蹈', 'urls': [
                'http://api.yujn.cn/api/shwd.php?type=video'
            ]},
            108: {'name': '又纯又欲', 'urls': [
                'https://api.317ak.com/API/sp/ycyy.php'
            ]},
            109: {'name': '腹肌变装', 'urls': [
                'https://api.317ak.com/API/sp/fjbz.php'
            ]},
            110: {'name': '完美身材', 'urls': [
                'http://api.yujn.cn/api/wmsc.php?type=video',
                'https://api.317ak.com/API/sp/wmsc.php'
            ]},
            111: {'name': '蛇姐系列', 'urls': [
                'http://api.yujn.cn/api/shejie.php?type=video'
            ]},
            112: {'name': '章若楠系', 'urls': [
                'http://api.yujn.cn/api/zrn.php?type=video'
            ]},
            113: {'name': '汉服系列', 'urls': [
                'http://api.yujn.cn/api/hanfu.php?type=video'
            ]},
            114: {'name': '杂鱼川系', 'urls': [
                'https://api.317ak.com/API/sp/zycx.php'
            ]},
            115: {'name': '慢摇系列', 'urls': [
                'http://api.yujn.cn/api/manyao.php?type=video',
                'https://api.317ak.com/API/sp/myxl.php'
            ]},
            116: {'name': '清纯系列', 'urls': [
                'http://api.yujn.cn/api/qingchun.php?type=video',
                'https://api.317ak.com/API/sp/qcxl.php'
            ]},
            117: {'name': 'COS系列', 'urls': [
                'http://api.yujn.cn/api/COS.php?type=video',
                'https://api.317ak.com/API/sp/cosxl.php'
            ]},
            118: {'name': '街拍系列', 'urls': [
                'http://api.yujn.cn/api/jiepai.php?type=video'
            ]},
            119: {'name': '余震系列', 'urls': [
                'https://api.317ak.com/API/sp/yzxl.php'
            ]},
            120: {'name': '你的欲梦', 'urls': [
                'http://api.yujn.cn/api/ndym.php?type=video',
                'https://api.317ak.com/API/sp/ndym.php'
            ]},
            121: {'name': '洛丽塔系', 'urls': [
                'http://api.yujn.cn/api/jksp.php?type=video'
            ]},
            122: {'name': '玉足美腿', 'urls': [
                'http://api.yujn.cn/api/yuzu.php?type=video',
                'https://api.317ak.com/API/sp/yzmt.php'
            ]},
            123: {'name': '清风皓月', 'urls': [
                'https://api.317ak.com/API/sp/qfhy.php'
            ]},
            124: {'name': '帅哥系列', 'urls': [
                'http://api.yujn.cn/api/xgg.php?type=video',
                'https://api.317ak.com/API/sp/sgxl.php'
            ]},
            125: {'name': '潇潇系列', 'urls': [
                'http://api.yujn.cn/api/xiaoxiao.php?',
                'https://api.317ak.com/API/sp/xxxl.php'
            ]},
            126: {'name': '倾梦推荐', 'urls': [
                'https://api.317ak.com/API/sp/qmtj.php'
            ]},
            127: {'name': '晴天推荐', 'urls': [
                'https://api.317ak.com/API/sp/qttj.php'
            ]},
            128: {'name': '琳铛系列', 'urls': [
                'https://api.317ak.com/API/sp/ldxl.php'
            ]}
        }

        logger.debug(f"[VideoDemand] 初始化配置完成")

        # 启动清理任务
        asyncio.create_task(self._schedule_cleanup())

    def _ensure_temp_dir(self, log=True):
        """确保临时目录存在"""
        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"[VideoDemand] 创建临时目录失败: {e}")
            # 如果无法创建指定目录，尝试使用系统临时目录
            import tempfile
            self.temp_dir = Path(tempfile.gettempdir()) / "VideoDemand"
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            logger.warning(f"[VideoDemand] 使用备用临时目录: {self.temp_dir}")

    async def _get_redirect_url(self, url: str) -> Optional[str]:
        """获取重定向URL"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        }

        try:
            async with httpx.AsyncClient(timeout=30.0, verify=False, follow_redirects=False) as client:
                response = await client.get(url, headers=headers)
                if response.status_code in (301, 302):
                    return response.headers.get('Location')
        except Exception:
            return None

        return None

    async def _get_random_video_url(self) -> str:
        """获取带有随机参数的视频URL

        Returns:
            str: 带有随机参数的视频URL
        """
        # 生成0到1之间的随机小数
        random_value = random.random()
        # 构建带有随机参数的URL
        random_url = f"{self.random_video_url}?_t={random_value}"
        return random_url

    async def _get_random_video(self) -> dict:
        """获取随机视频

        从现有的视频链接中随机选择一个

        Returns:
            dict: 包含视频信息的字典，成功时包含URL，失败时包含错误信息
        """
        try:
            # 从现有的视频类别中随机选择一个
            category_id = random.choice(list(self.api_mapping.keys()))
            category_name = self.api_mapping[category_id]['name']

            logger.info(f"随机选择了视频类别: {category_name} (ID: {category_id})")

            # 使用现有的_get_video方法获取该类别的视频
            result = await self._get_video(category_name)

            if result["success"]:
                logger.info(f"成功获取随机视频: {category_name}")
                # 添加类别信息到返回结果中
                result["category"] = category_name
                return result
            else:
                # 如果获取失败，尝试另一个随机类别
                logger.warning(f"获取{category_name}视频失败，尝试另一个类别")

                # 尝试最多5个不同的类别
                for _ in range(5):
                    # 排除已尝试过的类别
                    remaining_categories = [k for k in self.api_mapping.keys() if k != category_id]
                    if not remaining_categories:
                        break

                    category_id = random.choice(remaining_categories)
                    category_name = self.api_mapping[category_id]['name']
                    logger.info(f"重试随机选择了视频类别: {category_name} (ID: {category_id})")

                    result = await self._get_video(category_name)
                    if result["success"]:
                        logger.info(f"成功获取随机视频: {category_name}")
                        result["category"] = category_name
                        return result

                # 如果所有尝试都失败，返回错误
                return {"success": False, "message": "获取随机视频失败，请稍后重试"}

        except Exception as e:
            logger.error(f"获取随机视频时发生异常: {e}")
            return {"success": False, "message": "获取随机视频失败，请稍后重试"}

    async def _get_video(self, category_name: str, retry=6) -> dict:
        """获取视频URL"""
        category_id = None
        for key, value in self.api_mapping.items():
            if value['name'] == category_name:
                category_id = key
                break

        if category_id is None:
            return {"success": False, "message": f"未找到匹配的视频类别: {category_name}"}

        urls = self.api_mapping[category_id]['urls'].copy()
        max_error_count = 3

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        }

        while urls:
            url = urls[0]
            error_count = 0
            logger.debug(f"尝试获取视频 - 类别: {category_name}, URL: {url}")

            while error_count < max_error_count:
                try:
                    async with httpx.AsyncClient(timeout=30.0, verify=False, follow_redirects=True) as client:
                        response = await client.get(url, headers=headers)
                        response.raise_for_status()
                        final_url = str(response.url)
                        return {"success": True, "url": final_url}

                except (httpx.HTTPError, asyncio.TimeoutError) as e:
                    error_count += 1
                    if error_count >= max_error_count:
                        urls.pop(0)
                        break
                    await asyncio.sleep(2)

                except Exception as e:
                    error_count += 1
                    if error_count >= max_error_count:
                        urls.pop(0)
                        break
                    await asyncio.sleep(2)

        return {"success": False, "message": f"获取视频失败: 所有URL均无法获取视频，类别: {category_name}"}

    async def _download_video(self, url: str, category: str) -> Optional[str]:
        """下载视频到本地
        Returns:
            str: 本地视频文件路径,下载失败返回None
        """
        try:
            # 生成唯一文件名
            timestamp = int(time.time())
            filename = f"{category}_{timestamp}.mp4"
            filepath = self.temp_dir / filename

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }

            # 下载视频
            start_time = time.time()

            async with httpx.AsyncClient(timeout=300.0, verify=False) as client:  # 5分钟超时
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    return None

                content_length = len(response.content)
                duration = time.time() - start_time
                download_speed = content_length / duration / 1024 if duration > 0 else 0  # KB/s

                # 写入文件
                with open(filepath, 'wb') as f:
                    f.write(response.content)

            # 尝试修复视频元数据，确保时长信息正确
            try:
                await self._fix_video_metadata(filepath)
            except Exception as e:
                logger.warning(f"修复视频元数据失败，但继续使用原视频", exception=e)

            return str(filepath)

        except Exception as e:
            logger.error(f"下载视频失败: {url}, 类别: {category}", exception=e)
            return None

    async def _fix_video_metadata(self, video_path: str) -> None:
        """修复视频元数据，确保时长信息正确

        Args:
            video_path: 视频文件路径
        """
        try:
            # 创建临时文件路径
            temp_path = f"{video_path}.temp.mp4"

            # 1. 首先尝试最快的方式：直接复制流并优化元数据位置
            simple_cmd = f'ffmpeg -i "{video_path}" -c copy -movflags +faststart "{temp_path}"'
            logger.debug(f"执行简单修复命令: {simple_cmd}")

            simple_process = await asyncio.create_subprocess_shell(
                simple_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            simple_stdout, simple_stderr = await simple_process.communicate()

            if simple_process.returncode == 0:
                # 验证新文件是否可以正常打开
                verify_cmd = f'ffprobe -v error -select_streams v:0 -show_entries stream=duration -of default=noprint_wrappers=1:nokey=1 "{temp_path}"'
                logger.debug(f"执行验证命令: {verify_cmd}")

                verify_process = await asyncio.create_subprocess_shell(
                    verify_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                verify_stdout, verify_stderr = await verify_process.communicate()

                if verify_process.returncode == 0:
                    verify_duration = verify_stdout.decode().strip()
                    # 替换原文件
                    os.remove(video_path)
                    os.rename(temp_path, video_path)
                else:
                    verify_error = verify_stderr.decode()
                    logger.warning(f"简单处理后视频验证失败，尝试备用方法。错误: {verify_error}")
            else:
                simple_error = simple_stderr.decode()
                logger.warning(f"简单修复失败，尝试备用方法。错误: {simple_error}")

            # 2. 如果简单方法失败，尝试获取视频信息并进行最小必要的处理
            probe_cmd = f'ffprobe -v quiet -print_format json -show_format -show_streams "{video_path}"'
            logger.debug(f"执行探测命令: {probe_cmd}")

            probe_process = await asyncio.create_subprocess_shell(
                probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            probe_stdout, probe_stderr = await probe_process.communicate()

            if probe_process.returncode == 0:
                try:
                    probe_data = json.loads(probe_stdout.decode())
                    # 获取视频时长（秒）
                    duration = float(probe_data['format']['duration'])

                    # 获取视频编解码器信息
                    codec_info = "未知"
                    if 'streams' in probe_data:
                        for stream in probe_data['streams']:
                            if stream.get('codec_type') == 'video':
                                codec_info = f"{stream.get('codec_name', '未知')} {stream.get('width', '?')}x{stream.get('height', '?')}"
                                break

                    # 使用最小必要的处理参数
                    cmd = (
                        f'ffmpeg -i "{video_path}" '  # 输入文件
                        f'-c copy '  # 直接复制流，不重新编码
                        f'-movflags +faststart '  # 优化元数据位置
                        f'-metadata duration="{duration}" '  # 设置时长元数据
                        f'-y '  # 覆盖输出文件
                        f'"{temp_path}"'  # 输出文件
                    )
                    logger.debug(f"执行修复命令: {cmd}")

                    process = await asyncio.create_subprocess_shell(
                        cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )

                    stdout, stderr = await process.communicate()

                    if process.returncode == 0:
                        os.remove(video_path)
                        os.rename(temp_path, video_path)
                    else:
                        stderr_output = stderr.decode()
                        logger.warning(f"视频处理失败")
                        logger.debug(f"FFmpeg错误输出: {stderr_output}")
                        if os.path.exists(temp_path):
                            os.remove(temp_path)

                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.warning(f"解析视频信息失败", exception=e)
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

            else:
                probe_stderr_output = probe_stderr.decode()
                logger.warning(f"获取视频信息失败")
                logger.debug(f"FFprobe错误输出: {probe_stderr_output}")
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        except Exception as e:
            logger.error(f"修复视频元数据时出错", exception=e)
            # 确保临时文件被删除
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception as cleanup_error:
                    logger.warning(f"清理临时文件失败", exception=cleanup_error)

    async def _encode_video(self, video_path: str) -> Optional[str]:
        """将视频编码为base64
        Returns:
            str: base64编码后的视频数据,失败返回None
        """
        try:
            # 读取文件并编码
            with open(video_path, 'rb') as f:
                video_data = f.read()

            # 使用与VideoSender相同的编码方式
            base64_data = base64.b64encode(video_data).decode('utf-8')
            return base64_data

        except Exception as e:
            logger.error(f"视频编码失败: {video_path}", exception=e)
            return None

    def _extract_first_frame(self, video_path: str) -> Optional[str]:
        """从视频中提取第一帧并转换为base64
        使用插件 temp 目录下的子目录存放临时缩略图，
        提取成功后立即返回 base64 字符串，函数退出前清理临时文件。

        Returns:
            str: base64编码的图片数据,失败返回None
        """
        try:
            # 使用ffmpeg提取第一帧，与VideoSender保持一致
            temp_dir = str(self.temp_dir / "thumbnails")  # 在插件临时目录下创建子目录
            os.makedirs(temp_dir, exist_ok=True)
            thumbnail_path = os.path.join(temp_dir, f"temp_thumbnail_{int(time.time())}.jpg")

            # 执行ffmpeg命令提取第一帧
            process = subprocess.run([
                "ffmpeg",
                "-i", video_path,
                "-ss", "00:00:01",  # 从视频的第 1 秒开始提取
                "-vframes", "1",
                thumbnail_path,
                "-y"  # 如果文件存在，覆盖
            ], check=False, capture_output=True)

            if process.returncode != 0:
                logger.error(f"ffmpeg 执行失败: {process.stderr.decode()}")
                return None

            # 读取生成的缩略图
            if os.path.exists(thumbnail_path):
                with open(thumbnail_path, "rb") as image_file:
                    image_data = image_file.read()
                    image_base64 = base64.b64encode(image_data).decode("utf-8")
                    return image_base64
            else:
                logger.error(f"缩略图文件不存在: {thumbnail_path}")
                return None

        except Exception as e:
            logger.error(f"提取视频首帧失败: {video_path}", exception=e)
            return None
        finally:
            # 清理临时文件
            if 'temp_dir' in locals() and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)  # 递归删除临时文件夹
                except Exception as cleanup_error:
                    logger.error(f"清理缩略图临时文件失败: {cleanup_error}")

    async def _download_menu_image(self) -> Optional[bytes]:
        """加载菜单图片,返回图片二进制数据"""
        try:
            # 从本地文件加载图片
            # 尝试从插件目录加载图片
            local_image_path = os.path.join(self.plugin_dir, "menu.jpg")
            if os.path.exists(local_image_path):
                with open(local_image_path, 'rb') as f:
                    return f.read()
            else:
                logger.debug(f"插件目录中菜单图片不存在: {local_image_path}")
                # 如果本地文件不存在，尝试从网络下载
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(self.menu_image)
                    if response.status_code == 200:
                        return response.content
                    else:
                        return None
        except Exception as e:
            logger.error(f"加载菜单图片失败: {e}")
            return None

    @on_text_message
    async def handle_menu(self, bot: WechatAPIClient, message: dict):
        """处理菜单命令"""
        content = message.get("Content", "").strip()
        if content not in self.command:
            return True  # 不是菜单命令，继续执行后续处理

        wxid = message.get("FromWxid")
        roomid = message.get("FromGroup", wxid)

        # 检查菜单状态
        room_status = self.room_status.get(roomid, {})
        if room_status and time.time() - room_status.get("time", 0) < 5:  # 5秒内不重复发送
            await bot.send_text_message(roomid, "菜单已发送,请勿重复请求")
            return False  # 阻止后续处理

        # 发送菜单图片
        try:
            # 下载菜单图片
            image_data = await self._download_menu_image()
            if not image_data:
                await bot.send_text_message(roomid, "获取菜单失败,请稍后重试")
                return False  # 阻止后续处理

            # 直接发送二进制数据
            await bot.send_image_message(roomid, image_data)

            self.room_status[roomid] = {
                "time": time.time(),
                "expire": time.time() + self.cache_time,
                "notified": False
            }

        except Exception as e:
            logger.error(f"发送菜单失败: {e}")
            await bot.send_text_message(roomid, "发送菜单失败,请稍后重试")

        return False  # 阻止后续处理

    @on_text_message
    async def handle_video(self, bot: WechatAPIClient, message: dict):
        """处理视频请求"""
        content = message.get("Content", "").strip()

        # 严格匹配"看+数字"的格式
        if not re.match(r'^看\d+$', content):
            return True  # 不是视频请求命令，继续执行后续处理

        wxid = message.get("FromWxid")
        roomid = message.get("FromGroup", wxid)

        # 检查该房间是否发送过菜单且菜单在有效期内
        room_status = self.room_status.get(roomid)
        if not room_status:
            # 如果没有发送过菜单,直接忽略命令
            return True  # 继续执行后续处理

        if time.time() > room_status["expire"]:
            # 如果菜单已过期,提示重新获取菜单(只提示一次)
            if not room_status["notified"]:
                await bot.send_text_message(roomid, "菜单已过期,请重新发送「视频菜单」")
                room_status["notified"] = True
            return False  # 阻止后续处理

        # 检查当前并发数
        if self.video_semaphore.locked():
            await bot.send_text_message(roomid, "系统正在处理其他视频请求，请稍后再试...")
            return False  # 阻止后续处理

        video_path = None
        try:
            # 使用信号量控制并发
            async with self.video_semaphore:
                # 检查磁盘空间
                if not self._check_disk_space():
                    await bot.send_text_message(roomid, "系统存储空间不足，请稍后再试...")
                    return

                # 解析序号
                sequence = int(content[1:])  # 去掉"看"后转为数字
                if sequence < 1 or sequence > 128:
                    await bot.send_text_message(roomid, "无效的序号,请输入正确的序号(1-128)")
                    return

                category = self.api_mapping[sequence]["name"]

                await bot.send_text_message(
                    roomid,
                    f"正在获取{category}视频,请稍等..."
                )

                # 获取视频URL
                result = await self._get_video(category)
                if not result["success"]:
                    await bot.send_text_message(roomid, result["message"])
                    return

                video_url = result["url"]

                # 下载视频到本地
                video_path = await self._download_video(video_url, category)
                if not video_path:
                    await bot.send_text_message(roomid, "下载视频失败,请稍后重试")
                    return

                # 提取视频首帧作为封面
                cover_base64 = self._extract_first_frame(video_path)
                if cover_base64:
                    image_arg = cover_base64
                else:
                    logger.debug(f"提取视频首帧失败，将使用默认封面")
                    image_arg = None

                try:
                    probe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
                    logger.debug(f"执行命令: {probe_cmd}")

                    probe_process = await asyncio.create_subprocess_shell(
                        probe_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    probe_stdout, probe_stderr = await probe_process.communicate()

                    if probe_process.returncode == 0:
                        stdout_content = probe_stdout.decode().strip()

                        duration = float(stdout_content)
                        # 确保时长单位为秒
                        if duration > 1000:  # 如果值很大，可能是毫秒
                            video_duration = int(duration / 1000)
                        else:
                            video_duration = int(duration)
                    else:
                        stderr_content = probe_stderr.decode()
                        logger.debug(f"获取视频时长失败，进程返回码: {probe_process.returncode}, 错误: {stderr_content}")

                except Exception as e:
                    logger.warning(f"获取视频时长失败: {video_path}", exception=e)

                # 发送视频
                logger.debug(f"视频本地路径: {video_path}")
                logger.debug(f"图片 Base64 长度: {len(cover_base64) if cover_base64 else '无效'}")

                # 发送视频消息
                try:
                    send_result = await bot.send_video_message(
                        roomid,
                        video=video_path,
                        image=image_arg,
                    )
                    logger.info(f"视频发送成功: result={send_result}")
                    if cover_base64:
                        logger.debug(f"使用提取的视频首帧作为封面")
                except Exception as e:
                    logger.error(f"发送视频消息失败: {e}")
                    await bot.send_text_message(roomid, "发送视频失败，请稍后重试")
                    # 清理文件
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                        except Exception as cleanup_error:
                            logger.error(f"清理视频文件失败: {cleanup_error}")
                    return False

                # 发送成功后等待60秒再删除文件,确保视频已经完全发送
                if send_result:
                    logger.info(f"视频已成功发送，将在60秒后清理文件")
                    await asyncio.sleep(60)
                    try:
                        if os.path.exists(video_path):
                            os.remove(video_path)
                            logger.debug(f"清理视频文件成功: {video_path}")
                    except Exception as e:
                        logger.error(f"清理已发送的视频文件失败: {video_path}", exception=e)
                else:
                    logger.error("发送视频失败 - send_result为空")
                    await bot.send_text_message(roomid, "发送视频失败,请稍后重试")
                    # 发送失败的文件由定时清理任务处理

        except ValueError as e:
            logger.error(f"处理视频请求出现值错误", exception=e)
            await bot.send_text_message(roomid, "请输入正确的序号(1-128)")
        except Exception as e:
            logger.error(f"处理视频请求失败", exception=e)
            await bot.send_text_message(roomid, "处理请求失败,请稍后重试")
            # 发生异常时不立即删除文件,由定时清理任务处理

        return False  # 阻止后续处理

    @on_text_message
    async def handle_random_video(self, bot: WechatAPIClient, message: dict):
        """处理随机视频命令"""
        content = message.get("Content", "").strip()

        # 检查命令是否匹配
        if content not in self.random_command:
            return True  # 不是随机视频命令，继续执行后续处理

        wxid = message.get("FromWxid")
        roomid = message.get("FromGroup", wxid)

        # 检查并发数
        if self.video_semaphore.locked():
            await bot.send_text_message(roomid, "系统正在处理其他视频请求，请稍后再试...")
            return False  # 阻止后续处理

        video_path = None
        try:
            # 使用信号量控制并发
            async with self.video_semaphore:
                # 检查磁盘空间
                if not self._check_disk_space():
                    await bot.send_text_message(roomid, "系统存储空间不足，请稍后再试...")
                    return

                # 发送提示消息
                await bot.send_text_message(roomid, "正在获取随机视频，请稍等...")

                # 获取随机视频URL
                result = await self._get_random_video()
                if not result["success"]:
                    await bot.send_text_message(roomid, result["message"])
                    return

                video_url = result["url"]
                category = result.get("category", "随机视频")

                # 更新提示消息
                await bot.send_text_message(
                    roomid,
                    f"正在获取{category}视频,请稍等..."
                )

                # 下载视频到本地
                video_path = await self._download_video(video_url, category)
                if not video_path:
                    await bot.send_text_message(roomid, "下载视频失败，请稍后重试")
                    return

                # 提取视频首帧作为封面
                cover_base64 = self._extract_first_frame(video_path)
                if cover_base64:
                    image_arg = cover_base64
                else:
                    logger.debug(f"提取随机视频首帧失败，将使用默认封面")
                    image_arg = None

                # 尝试获取视频时长信息
                video_duration = None
                try:
                    probe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
                    probe_process = await asyncio.create_subprocess_shell(
                        probe_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    probe_stdout, probe_stderr = await probe_process.communicate()
                    if probe_process.returncode == 0:
                        duration = float(probe_stdout.decode().strip())
                        # 确保时长单位为秒
                        if duration > 1000:  # 如果值很大，可能是毫秒
                            video_duration = int(duration / 1000)
                        else:
                            video_duration = int(duration)
                except Exception as e:
                    logger.warning(f"获取随机视频时长失败: {e}")

                # 发送视频
                logger.debug(f"视频本地路径: {video_path}")
                logger.debug(f"图片 Base64 长度: {len(cover_base64) if cover_base64 else '无效'}")

                # 发送视频消息
                try:
                    send_result = await bot.send_video_message(
                        roomid,
                        video=video_path,
                        image=image_arg,
                    )
                    logger.info(f"视频发送成功: result={send_result}")
                except Exception as e:
                    logger.error(f"发送视频消息失败: {e}")
                    await bot.send_text_message(roomid, "发送视频失败，请稍后重试")
                    # 清理文件
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                        except Exception as cleanup_error:
                            logger.error(f"清理视频文件失败: {cleanup_error}")
                    return False

                # 发送成功后等待60秒再删除文件,确保视频已经完全发送
                if send_result:
                    logger.info(f"视频已成功发送，将在60秒后清理文件")
                    await asyncio.sleep(60)
                    try:
                        if os.path.exists(video_path):
                            os.remove(video_path)
                            logger.debug(f"清理视频文件成功: {video_path}")
                    except Exception as e:
                        logger.error(f"清理已发送的视频文件失败: {video_path}", exception=e)
                else:
                    logger.error("发送视频失败 - send_result为空")
                    await bot.send_text_message(roomid, "发送视频失败，请稍后重试")
                    # 发送失败的文件由定时清理任务处理

        except Exception as e:
            logger.error(f"处理随机视频请求失败", exception=e)
            await bot.send_text_message(roomid, "处理请求失败，请稍后重试")
            # 发生异常时不立即删除文件，由定时清理任务处理

        return False  # 阻止后续处理

    @on_text_message
    async def handle_url_video(self, bot: WechatAPIClient, message: dict):
        """处理直接通过URL获取视频的请求"""
        content = message.get("Content", "").strip()

        # 检查是否是视频URL
        if not content.startswith(self.random_video_url):
            return True  # 不是URL视频命令，继续执行后续处理

        wxid = message.get("FromWxid")
        roomid = message.get("FromGroup", wxid)

        # 检查并发数
        if self.video_semaphore.locked():
            await bot.send_text_message(roomid, "系统正在处理其他视频请求，请稍后再试...")
            return False  # 阻止后续处理

        video_path = None
        try:
            # 使用信号量控制并发
            async with self.video_semaphore:
                # 检查磁盘空间
                if not self._check_disk_space():
                    await bot.send_text_message(roomid, "系统存储空间不足，请稍后再试...")
                    return

                # 发送提示消息
                await bot.send_text_message(roomid, "正在获取视频，请稍等...")

                # 直接使用提供的URL
                url = content

                # 检查URL是否包含_t参数，如果没有则添加
                if "_t=" not in url:
                    random_value = random.random()
                    url = f"{url}?_t={random_value}" if "?" not in url else f"{url}&_t={random_value}"

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; V2002A Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/83.0.4103.106 Mobile Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Encoding': 'identity;q=1, *;q=0',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                    'X-Requested-With': 'tib.zaybmluk.rbvkwmw',
                    'Sec-Fetch-Site': 'same-origin',
                    'Sec-Fetch-Mode': 'no-cors',
                    'Sec-Fetch-Dest': 'video',
                    'Range': 'bytes=0-',
                    'Pragma': 'no-cache',
                    'Cache-Control': 'no-cache'
                }

                # 尝试获取重定向URL
                try:
                    async with httpx.AsyncClient(timeout=30.0, verify=False, follow_redirects=True) as client:
                        response = await client.get(url, headers=headers)
                        response.raise_for_status()

                        # 获取最终的URL
                        final_url = str(response.url)

                        # 下载视频到本地
                        video_path = await self._download_video(final_url, "URL视频")
                        if not video_path:
                            await bot.send_text_message(roomid, "下载视频失败，请稍后重试")
                            return

                except Exception as e:
                    logger.error(f"获取视频URL失败: {e}")
                    await bot.send_text_message(roomid, "获取视频失败，请确认链接有效")
                    return

                # 提取视频首帧作为封面
                cover_base64 = self._extract_first_frame(video_path)
                if cover_base64:
                    image_arg = cover_base64
                else:
                    logger.debug(f"提取URL视频首帧失败，将使用默认封面")
                    image_arg = None

                # 尝试获取视频时长信息
                video_duration = None
                try:
                    probe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
                    probe_process = await asyncio.create_subprocess_shell(
                        probe_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    probe_stdout, probe_stderr = await probe_process.communicate()
                    if probe_process.returncode == 0:
                        duration = float(probe_stdout.decode().strip())
                        # 确保时长单位为秒
                        if duration > 1000:  # 如果值很大，可能是毫秒
                            video_duration = int(duration / 1000)
                        else:
                            video_duration = int(duration)
                except Exception as e:
                    logger.warning(f"获取URL视频时长失败: {e}")

                # 发送视频
                logger.debug(f"视频本地路径: {video_path}")
                logger.debug(f"图片 Base64 长度: {len(cover_base64) if cover_base64 else '无效'}")

                # 发送视频消息
                try:
                    send_result = await bot.send_video_message(
                        roomid,
                        video=video_path,
                        image=image_arg,
                    )
                    logger.info(f"视频发送成功: result={send_result}")
                except Exception as e:
                    logger.error(f"发送视频消息失败: {e}")
                    await bot.send_text_message(roomid, "发送视频失败，请稍后重试")
                    # 清理文件
                    if video_path and os.path.exists(video_path):
                        try:
                            os.remove(video_path)
                        except Exception as cleanup_error:
                            logger.error(f"清理视频文件失败: {cleanup_error}")
                    return False

                # 发送成功后等待60秒再删除文件,确保视频已经完全发送
                if send_result:
                    logger.info(f"视频已成功发送，将在60秒后清理文件")
                    await asyncio.sleep(60)
                    try:
                        if os.path.exists(video_path):
                            os.remove(video_path)
                            logger.debug(f"清理视频文件成功: {video_path}")
                    except Exception as e:
                        logger.error(f"清理已发送的视频文件失败: {video_path}", exception=e)
                else:
                    logger.error("发送视频失败 - send_result为空")
                    await bot.send_text_message(roomid, "发送视频失败，请稍后重试")
                    # 发送失败的文件由定时清理任务处理

        except Exception as e:
            logger.error(f"处理URL视频请求失败: {e}")
            await bot.send_text_message(roomid, "处理请求失败，请稍后重试")
            # 发生异常时不立即删除文件，由定时清理任务处理

        return False  # 阻止后续处理

    def _check_disk_space(self) -> bool:
        """检查磁盘空间是否足够

        Returns:
            bool: 空间足够返回True，否则返回False
        """
        try:
            # 获取临时目录所在磁盘的可用空间
            free_space = shutil.disk_usage(self.temp_dir).free
            if free_space < self.min_disk_space:
                # 空间不足时，尝试紧急清理所有临时文件
                self._emergency_cleanup()
                # 重新检查空间
                free_space = shutil.disk_usage(self.temp_dir).free
                return free_space >= self.min_disk_space
            return True
        except Exception as e:
            logger.error(f"检查磁盘空间失败: {e}")
            # 发生异常时保守返回True
            return True

    def _emergency_cleanup(self):
        """紧急清理所有临时文件"""
        try:
            for file in self.temp_dir.glob("*.mp4"):
                try:
                    file.unlink()
                except Exception as e:
                    logger.error(f"紧急清理文件失败 {file}: {e}")
        except Exception as e:
            logger.error(f"紧急清理临时文件失败: {e}")

    async def cleanup_temp_files(self):
        """清理临时文件"""
        try:
            # 清理1小时前的临时文件
            current_time = time.time()
            for file in self.temp_dir.glob("*.mp4"):
                if current_time - file.stat().st_mtime > 3600:  # 1小时
                    try:
                        file.unlink()
                    except Exception as e:
                        logger.error(f"清理文件失败 {file}: {e}")

        except Exception as e:
            logger.error(f"清理临时文件失败: {e}")
            # 发生异常时才检查目录是否存在
            self._ensure_temp_dir(log=False)  # 异常情况下不输出日志避免日志爆炸

    async def _schedule_cleanup(self):
        """调度清理任务"""
        while True:
            try:
                await asyncio.sleep(1800)  # 每30分钟清理一次
                await self.cleanup_temp_files()
            except Exception as e:
                logger.error(f"调度清理任务失败: {e}")
                # 出错后等待短暂时间再继续循环
                await asyncio.sleep(60)
