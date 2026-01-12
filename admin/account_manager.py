"""
账号管理模块
提供多微信账号管理的功能
"""

import os
import json
import shutil
import time
import sqlite3
from pathlib import Path
from typing import Dict, Optional, List
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from loguru import logger

# 创建路由器
router = APIRouter(prefix="/api/accounts", tags=["accounts"])

# 认证检查函数（将在注册路由时设置）
check_auth = None

# 更新机器人状态的函数（将在注册路由时设置）
update_bot_status = None

# 重启系统的函数（将在注册路由时设置）
restart_system = None

# 账号存储目录
ACCOUNTS_DIR = Path("resource") / "accounts"

# 账号列表文件
ACCOUNT_LIST_FILE = ACCOUNTS_DIR / "account_list.json"

# 当前活动账号文件
ACTIVE_ACCOUNT_FILE = Path("resource") / "robot_stat.json"

# 数据库文件列表
DATABASE_FILES = [
    Path("database") / "xybot.db",
    Path("database") / "keyval.db",
    Path("database") / "message.db",
    Path("database") / "contacts.db"
]

def ensure_accounts_dir():
    """确保账号存储目录存在"""
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    # 确保账号列表文件存在
    if not ACCOUNT_LIST_FILE.exists():
        with open(ACCOUNT_LIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"accounts": [], "current": ""}, f, ensure_ascii=False, indent=2)
        logger.info(f"创建账号列表文件: {ACCOUNT_LIST_FILE}")
    else:
        # 更新现有账号数据结构
        update_existing_accounts()

def update_existing_accounts():
    """更新现有账号数据结构"""
    try:
        # 读取账号列表文件
        if not ACCOUNT_LIST_FILE.exists():
            return

        with open(ACCOUNT_LIST_FILE, "r", encoding="utf-8") as f:
            account_list = json.load(f)

        # 检查每个账号并更新
        updated = False
        for account in account_list.get("accounts", []):
            # 检查是否需要添加微信号字段
            if "alias" not in account and "wxid" in account:
                account["alias"] = ""
                updated = True

            # 检查是否需要添加头像字段
            if "avatar_url" not in account and "wxid" in account:
                account["avatar_url"] = get_bot_avatar(account["wxid"])
                updated = True

            # 检查是否需要添加设备名字段
            if "device_name" not in account:
                account["device_name"] = ""
                updated = True

        # 如果有更新，保存账号列表
        if updated:
            with open(ACCOUNT_LIST_FILE, "w", encoding="utf-8") as f:
                json.dump(account_list, f, ensure_ascii=False, indent=2)
            logger.info(f"更新现有账号数据结构成功")

        # 检查并更新单个账号文件
        for account in account_list.get("accounts", []):
            if "wxid" in account and "file_name" in account:
                account_file = ACCOUNTS_DIR / account["file_name"]
                if account_file.exists():
                    try:
                        with open(account_file, "r", encoding="utf-8") as f:
                            account_data = json.load(f)

                        # 检查是否需要更新字段
                        account_updated = False

                        if "alias" not in account_data and "wxid" in account_data:
                            account_data["alias"] = ""
                            account_updated = True

                        if "avatar_url" not in account_data and "wxid" in account_data:
                            account_data["avatar_url"] = get_bot_avatar(account_data["wxid"])
                            account_updated = True

                        if "device_name" not in account_data:
                            account_data["device_name"] = ""
                            account_updated = True

                        # 如果有更新，保存账号文件
                        if account_updated:
                            with open(account_file, "w", encoding="utf-8") as f:
                                json.dump(account_data, f, ensure_ascii=False, indent=2)
                            logger.info(f"更新账号文件成功: {account_file}")
                    except Exception as e:
                        logger.error(f"更新账号文件失败: {account_file}, 错误: {e}")
    except Exception as e:
        logger.error(f"更新现有账号数据结构失败: {e}")

def get_account_list() -> Dict:
    """获取账号列表"""
    ensure_accounts_dir()

    try:
        with open(ACCOUNT_LIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"读取账号列表失败: {e}")
        # 返回空列表
        return {"accounts": [], "current": ""}

def save_account_list(account_list: Dict) -> bool:
    """保存账号列表"""
    ensure_accounts_dir()

    try:
        with open(ACCOUNT_LIST_FILE, "w", encoding="utf-8") as f:
            json.dump(account_list, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存账号列表失败: {e}")
        return False

def get_current_account() -> Optional[Dict]:
    """获取当前活动账号信息"""
    if not ACTIVE_ACCOUNT_FILE.exists():
        return None

    try:
        with open(ACTIVE_ACCOUNT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 检查是否有wxid
        if not data.get("wxid"):
            return None

        # 如果 robot_stat.json 中没有昵称或微信号，尝试从状态文件中获取
        if not data.get("nickname") or not data.get("alias"):
            # 读取状态文件
            status_file = Path("admin") / "bot_status.json"
            if status_file.exists():
                try:
                    with open(status_file, "r", encoding="utf-8") as f:
                        status_data = json.load(f)

                    # 获取昵称和微信号
                    if not data.get("nickname") and status_data.get("nickname"):
                        data["nickname"] = status_data.get("nickname")
                    if not data.get("alias") and status_data.get("alias"):
                        data["alias"] = status_data.get("alias")

                    # 将更新后的数据保存回 robot_stat.json
                    with open(ACTIVE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info(f"从状态文件更新了账号信息: {data.get('nickname')}, {data.get('alias')}")
                except Exception as e:
                    logger.error(f"从状态文件获取账号信息失败: {e}")

        return data
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"读取当前账号信息失败: {e}")
        return None

def save_current_account_to_list() -> bool:
    """保存当前账号到账号列表"""
    current_account = get_current_account()
    if not current_account:
        logger.info("当前没有活动账号，无需保存")
        return False

    wxid = current_account.get("wxid")
    if not wxid:
        logger.info("当前账号没有wxid，无需保存")
        return False

    # 获取账号列表
    account_list = get_account_list()

    # 创建账号目录
    account_dir = ACCOUNTS_DIR / wxid
    account_dir.mkdir(parents=True, exist_ok=True)

    # 创建数据库目录
    db_dir = account_dir / "database"
    db_dir.mkdir(parents=True, exist_ok=True)

    # 检查账号是否已存在
    account_exists = False
    for account in account_list["accounts"]:
        if account["wxid"] == wxid:
            account_exists = True
            # 更新账号信息
            account["last_login"] = time.time()

            # 更新昵称、微信号和头像信息
            if current_account.get("nickname"):
                account["nickname"] = current_account.get("nickname")
            if current_account.get("alias"):
                account["alias"] = current_account.get("alias")
            if current_account.get("avatar_url"):
                account["avatar_url"] = current_account.get("avatar_url")
            if current_account.get("device_name"):
                account["device_name"] = current_account.get("device_name")

            # 保存账号文件
            account_file = account_dir / "account.json"
            try:
                shutil.copy2(ACTIVE_ACCOUNT_FILE, account_file)
                logger.info(f"更新账号文件: {account_file}")

                # 备份数据库文件
                for db_file in DATABASE_FILES:
                    if db_file.exists():
                        target_db_file = db_dir / db_file.name
                        try:
                            shutil.copy2(db_file, target_db_file)
                            logger.info(f"备份数据库文件: {db_file} -> {target_db_file}")
                        except Exception as e:
                            logger.error(f"备份数据库文件失败: {db_file}, 错误: {e}")
            except Exception as e:
                logger.error(f"更新账号文件失败: {e}")
                return False
            break

    # 如果账号不存在，添加到列表
    if not account_exists:
        # 获取昵称、微信号和头像
        nickname = current_account.get("nickname", "未命名账号")
        alias = current_account.get("alias", "")
        avatar_url = current_account.get("avatar_url", "")
        device_name = current_account.get("device_name", "")

        # 创建新账号记录
        new_account = {
            "wxid": wxid,
            "nickname": nickname,
            "alias": alias,
            "avatar_url": avatar_url,
            "device_name": device_name,
            "last_login": time.time(),
            "file_name": f"{wxid}/account.json"
        }

        # 添加到列表
        account_list["accounts"].append(new_account)

        # 保存账号文件
        account_file = account_dir / "account.json"
        try:
            shutil.copy2(ACTIVE_ACCOUNT_FILE, account_file)
            logger.info(f"创建账号文件: {account_file}")

            # 备份数据库文件
            for db_file in DATABASE_FILES:
                if db_file.exists():
                    target_db_file = db_dir / db_file.name
                    try:
                        shutil.copy2(db_file, target_db_file)
                        logger.info(f"备份数据库文件: {db_file} -> {target_db_file}")
                    except Exception as e:
                        logger.error(f"备份数据库文件失败: {db_file}, 错误: {e}")
        except Exception as e:
            logger.error(f"创建账号文件失败: {e}")
            return False

    # 更新当前账号
    account_list["current"] = wxid

    # 保存账号列表
    return save_account_list(account_list)

@router.get("/list")
async def api_get_account_list(request: Request):
    """获取账号列表API"""
    # 检查认证状态
    try:
        username = await check_auth(request)
        if not username:
            logger.error("获取账号列表失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        # 获取账号列表
        account_list = get_account_list()

        # 获取当前账号
        current_account = get_current_account()
        current_wxid = current_account.get("wxid", "") if current_account else ""

        # 更新当前账号标记
        account_list["current"] = current_wxid

        # 格式化时间并添加头像路径
        for account in account_list["accounts"]:
            if "last_login" in account:
                # 转换时间戳为可读格式
                account["last_login_formatted"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(account["last_login"])
                )

            # 获取头像路径
            if "wxid" in account:
                # 尝试下载头像
                try:
                    await download_avatar(account["wxid"])
                except Exception as e:
                    logger.error(f"下载头像失败: {e}")
                # 获取头像路径
                account["avatar_url"] = get_bot_avatar(account["wxid"])

        # 如果当前有登录的账号，尝试从API获取最新的昵称和头像
        if current_wxid:
            try:
                # 调用API获取最新的用户信息
                user_info = await get_user_profile(current_wxid)
                if user_info:
                    # 更新当前账号的昵称和头像
                    for account in account_list["accounts"]:
                        if account["wxid"] == current_wxid:
                            # 更新昵称
                            if user_info.get("nickname"):
                                account["nickname"] = user_info["nickname"]
                                # 同时更新robot_stat.json中的昵称
                                if current_account:
                                    current_account["nickname"] = user_info["nickname"]
                                    with open(ACTIVE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                                        json.dump(current_account, f, ensure_ascii=False, indent=2)
                            # 更新头像
                            if user_info.get("avatar_downloaded"):
                                account["avatar_url"] = get_bot_avatar(current_wxid)
                            break
            except Exception as e:
                logger.error(f"从API获取用户信息失败: {e}")

        return JSONResponse(content={
            "success": True,
            "data": account_list
        })
    except Exception as e:
        logger.error(f"获取账号列表失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

@router.post("/switch/{wxid}")
async def api_switch_account(wxid: str, request: Request):
    """切换账号API"""
    # 检查认证状态
    try:
        username = await check_auth(request)
        if not username:
            logger.error("切换账号失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        # 保存当前账号
        save_current_account_to_list()

        # 获取账号列表
        account_list = get_account_list()

        # 如果wxid为"new"，则创建新账号
        if wxid == "new":
            logger.info("创建新账号")

            # 创建空的robot_stat.json文件
            empty_account = {"wxid": "", "device_name": "", "device_id": ""}
            try:
                with open(ACTIVE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                    json.dump(empty_account, f, ensure_ascii=False, indent=2)
                logger.info(f"创建空账号文件: {ACTIVE_ACCOUNT_FILE}")

                # 先删除所有当前数据库文件
                for db_file in DATABASE_FILES:
                    if db_file.exists():
                        try:
                            # 关闭数据库连接，确保文件可以被删除
                            try:
                                conn = sqlite3.connect(str(db_file))
                                conn.close()
                            except Exception as e:
                                logger.warning(f"关闭数据库连接失败: {db_file}, 错误: {e}")

                            # 删除数据库文件
                            os.remove(db_file)
                            logger.info(f"删除旧数据库文件: {db_file}")
                        except Exception as e:
                            logger.error(f"删除旧数据库文件失败: {db_file}, 错误: {e}")

                # 创建新的空数据库文件
                for db_file in DATABASE_FILES:
                    try:
                        # 创建空数据库文件
                        conn = sqlite3.connect(str(db_file))
                        conn.close()
                        logger.info(f"创建新的空数据库文件: {db_file}")
                    except Exception as e:
                        logger.error(f"创建新的空数据库文件失败: {db_file}, 错误: {e}")
            except Exception as e:
                logger.error(f"创建空账号文件失败: {e}")
                return JSONResponse(content={"success": False, "error": f"创建新账号失败: {str(e)}"})

            # 更新状态
            if update_bot_status:
                update_bot_status("offline", "已创建新账号，请扫码登录")

            # 重启系统
            if restart_system:
                try:
                    logger.info(f"准备重启系统，restart_system函数: {restart_system}")
                    # 使用await等待restart_system协程完成
                    await restart_system()
                    logger.info("已调用restart_system函数，系统应该开始重启")
                except Exception as e:
                    logger.error(f"调用restart_system函数失败: {e}")
                    return JSONResponse(content={
                        "success": False,
                        "error": f"重启系统失败: {str(e)}"
                    })

                return JSONResponse(content={
                    "success": True,
                    "message": "已创建新账号，系统正在重启，请稍后扫码登录"
                })
            else:
                logger.error("重启函数未设置，无法完成账号切换")
                return JSONResponse(content={
                    "success": False,
                    "error": "重启函数未设置，无法完成账号切换"
                })

        # 查找要切换的账号
        target_account = None
        for account in account_list["accounts"]:
            if account["wxid"] == wxid:
                target_account = account
                break

        if not target_account:
            return JSONResponse(content={
                "success": False,
                "error": f"账号 {wxid} 不存在"
            })

        # 获取账号目录和文件路径
        account_dir = ACCOUNTS_DIR / wxid
        account_file = account_dir / "account.json"
        db_dir = account_dir / "database"

        # 检查是否存在旧格式的账号文件
        old_account_file = ACCOUNTS_DIR / f"{wxid}.json"
        if not account_file.exists() and old_account_file.exists():
            logger.info(f"发现旧格式账号文件: {old_account_file}")
            # 创建新的目录结构
            account_dir.mkdir(parents=True, exist_ok=True)
            db_dir.mkdir(parents=True, exist_ok=True)

            # 复制旧账号文件到新位置
            try:
                shutil.copy2(old_account_file, account_file)
                logger.info(f"将旧格式账号文件转换为新格式: {old_account_file} -> {account_file}")

                # 更新账号列表中的文件路径
                if target_account:
                    target_account["file_name"] = f"{wxid}/account.json"
                    save_account_list(account_list)
                    logger.info(f"更新账号列表中的文件路径: {target_account['file_name']}")
            except Exception as e:
                logger.error(f"转换旧格式账号文件失败: {e}")
                return JSONResponse(content={"success": False, "error": f"转换账号文件失败: {str(e)}"})

        if not account_file.exists():
            logger.error(f"账号文件不存在: {account_file}")
            return JSONResponse(content={
                "success": False,
                "error": f"账号文件 {account_file} 不存在"
            })

        # 复制账号文件到活动账号文件
        try:
            # 复制账号文件
            shutil.copy2(account_file, ACTIVE_ACCOUNT_FILE)
            logger.info(f"切换到账号: {wxid}")

            # 先删除所有当前数据库文件
            for db_file in DATABASE_FILES:
                if db_file.exists():
                    try:
                        # 关闭数据库连接，确保文件可以被删除
                        try:
                            conn = sqlite3.connect(str(db_file))
                            conn.close()
                        except Exception as e:
                            logger.warning(f"关闭数据库连接失败: {db_file}, 错误: {e}")

                        # 删除数据库文件
                        os.remove(db_file)
                        logger.info(f"删除旧数据库文件: {db_file}")
                    except Exception as e:
                        logger.error(f"删除旧数据库文件失败: {db_file}, 错误: {e}")

            # 清除内存中的联系人缓存
            try:
                # 尝试导入并清除联系人缓存
                from database.contacts_db import clear_contacts_cache
                clear_contacts_cache()
                logger.info("清除联系人缓存成功")
            except Exception as e:
                logger.warning(f"清除联系人缓存失败: {e}")

            # 恢复数据库文件
            if db_dir.exists():
                # 从目标账号的备份中恢复数据库文件
                for db_file in DATABASE_FILES:
                    source_db_file = db_dir / db_file.name
                    if source_db_file.exists():
                        try:
                            # 复制数据库文件
                            shutil.copy2(source_db_file, db_file)
                            logger.info(f"恢复数据库文件: {source_db_file} -> {db_file}")
                        except Exception as e:
                            logger.error(f"恢复数据库文件失败: {source_db_file}, 错误: {e}")
                    else:
                        # 如果没有备份文件，创建新的空数据库文件
                        try:
                            conn = sqlite3.connect(str(db_file))
                            conn.close()
                            logger.info(f"创建新的空数据库文件: {db_file}")
                        except Exception as e:
                            logger.error(f"创建新的空数据库文件失败: {db_file}, 错误: {e}")
            else:
                # 如果目标账号没有数据库目录，创建新的空数据库文件
                for db_file in DATABASE_FILES:
                    try:
                        conn = sqlite3.connect(str(db_file))
                        conn.close()
                        logger.info(f"创建新的空数据库文件: {db_file}")
                    except Exception as e:
                        logger.error(f"创建新的空数据库文件失败: {db_file}, 错误: {e}")
        except Exception as e:
            logger.error(f"复制账号文件失败: {e}")
            return JSONResponse(content={"success": False, "error": f"切换账号失败: {str(e)}"})

        # 更新当前账号
        account_list["current"] = wxid
        target_account["last_login"] = time.time()
        save_account_list(account_list)

        # 更新状态
        if update_bot_status:
            update_bot_status("switching", f"正在切换到账号: {target_account.get('nickname', wxid)}")

        # 重启系统
        if restart_system:
            try:
                logger.info(f"准备重启系统，restart_system函数: {restart_system}")
                # 使用await等待restart_system协程完成
                await restart_system()
                logger.info("已调用restart_system函数，系统应该开始重启")
            except Exception as e:
                logger.error(f"调用restart_system函数失败: {e}")
                return JSONResponse(content={
                    "success": False,
                    "error": f"重启系统失败: {str(e)}"
                })

            return JSONResponse(content={
                "success": True,
                "message": f"正在切换到账号: {target_account.get('nickname', wxid)}，系统正在重启"
            })
        else:
            logger.error("重启函数未设置，无法完成账号切换")
            return JSONResponse(content={
                "success": False,
                "error": "重启函数未设置，无法完成账号切换"
            })
    except Exception as e:
        logger.error(f"切换账号失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

@router.get("/refresh/{wxid}")
async def api_refresh_account(wxid: str, request: Request):
    """刷新账号信息API"""
    # 检查认证状态
    try:
        username = await check_auth(request)
        if not username:
            logger.error("刷新账号信息失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        # 从API获取最新的用户信息
        user_info = await get_user_profile(wxid)
        if not user_info:
            return JSONResponse(content={
                "success": False,
                "error": "获取用户信息失败"
            })

        # 获取账号列表
        account_list = get_account_list()

        # 查找并更新账号信息
        account_updated = False
        for account in account_list["accounts"]:
            if account["wxid"] == wxid:
                # 更新昵称
                if user_info.get("nickname"):
                    account["nickname"] = user_info["nickname"]
                    account_updated = True

                # 更新微信号
                if user_info.get("alias"):
                    account["alias"] = user_info["alias"]
                    account_updated = True

                # 更新头像
                if user_info.get("avatar_downloaded"):
                    account["avatar_url"] = get_bot_avatar(wxid)
                    account_updated = True

                break

        # 如果有更新，保存账号列表
        if account_updated:
            save_account_list(account_list)

            # 如果是当前账号，同时更新robot_stat.json
            current_account = get_current_account()
            if current_account and current_account.get("wxid") == wxid:
                if user_info.get("nickname"):
                    current_account["nickname"] = user_info["nickname"]
                if user_info.get("alias"):
                    current_account["alias"] = user_info["alias"]

                # 保存更新后的数据
                with open(ACTIVE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                    json.dump(current_account, f, ensure_ascii=False, indent=2)

        return JSONResponse(content={
            "success": True,
            "data": {
                "nickname": user_info.get("nickname", ""),
                "alias": user_info.get("alias", ""),
                "avatar_url": get_bot_avatar(wxid) if user_info.get("avatar_downloaded") else ""
            }
        })
    except Exception as e:
        logger.error(f"刷新账号信息失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

@router.delete("/{wxid}")
async def api_delete_account(wxid: str, request: Request):
    """删除账号API"""
    # 检查认证状态
    try:
        username = await check_auth(request)
        if not username:
            logger.error("删除账号失败：未认证")
            return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

        # 获取账号列表
        account_list = get_account_list()

        # 获取当前账号
        current_account = get_current_account()
        current_wxid = current_account.get("wxid", "") if current_account else ""

        # 不允许删除当前账号
        if wxid == current_wxid:
            return JSONResponse(content={
                "success": False,
                "error": "不能删除当前使用的账号"
            })

        # 查找要删除的账号
        account_index = -1
        for i, account in enumerate(account_list["accounts"]):
            if account["wxid"] == wxid:
                account_index = i
                break

        if account_index == -1:
            return JSONResponse(content={
                "success": False,
                "error": f"账号 {wxid} 不存在"
            })

        # 删除账号目录及文件
        account_dir = ACCOUNTS_DIR / wxid
        if account_dir.exists():
            try:
                # 使用shutil.rmtree删除整个目录
                shutil.rmtree(account_dir)
                logger.info(f"删除账号目录: {account_dir}")
            except Exception as e:
                logger.error(f"删除账号目录失败: {e}")
                return JSONResponse(content={"success": False, "error": f"删除账号目录失败: {str(e)}"})

        # 从列表中删除账号
        del account_list["accounts"][account_index]

        # 保存账号列表
        save_account_list(account_list)

        return JSONResponse(content={
            "success": True,
            "message": f"已删除账号: {wxid}"
        })
    except Exception as e:
        logger.error(f"删除账号失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

async def get_user_profile(wxid: str) -> dict:
    """从API获取用户信息

    Args:
        wxid: 微信ID

    Returns:
        dict: 包含用户昵称、头像等信息的字典
    """
    try:
        import aiohttp

        # 调用API获取用户信息
        api_port = 9011  # 默认PAD API端口

        # 准备两种API路径
        # /VXAPI 路径 (849协议)
        url_849 = f'http://127.0.0.1:{api_port}/VXAPI/User/GetContractProfile?wxid={wxid}'
        # /api 路径 (855协议)
        url_855 = f'http://127.0.0.1:{api_port}/api/User/GetContractProfile?wxid={wxid}'

        # 首先尝试使用 /VXAPI 路径 (849协议)
        url = url_849
        logger.info(f"从API获取用户信息(849协议): {url}")

        async with aiohttp.ClientSession() as session:
            # 尝试使用 /VXAPI 路径
            try:
                async with session.post(url) as response:
                    if response.status == 200:
                        json_resp = await response.json()
                        logger.info(f"获取到用户信息响应(849协议): {json_resp.get('Success')}")

                        if json_resp.get("Success") and json_resp.get("Data"):
                            data = json_resp.get("Data")
                            result = {}

                            # 提取昵称
                            if data.get("userInfo") and data["userInfo"].get("NickName") and data["userInfo"]["NickName"].get("string"):
                                result["nickname"] = data["userInfo"]["NickName"]["string"]
                                logger.info(f"成功获取昵称: {result['nickname']}")

                            # 提取微信号
                            if data.get("userInfo") and data["userInfo"].get("Alias"):
                                result["alias"] = data["userInfo"]["Alias"]
                                logger.info(f"成功获取微信号: {result['alias']}")

                            # 提取头像 URL
                            if data.get("userInfoExt") and data["userInfoExt"].get("BigHeadImgUrl"):
                                head_img_url = data["userInfoExt"]["BigHeadImgUrl"]
                                logger.info(f"成功获取头像 URL: {head_img_url}")

                                # 下载头像
                                avatar_dir = Path("resource") / "avatars"
                                avatar_dir.mkdir(parents=True, exist_ok=True)
                                avatar_file = avatar_dir / f"{wxid}.jpg"

                                async with session.get(head_img_url) as img_response:
                                    if img_response.status == 200:
                                        image_data = await img_response.read()
                                        # 保存头像文件
                                        with open(avatar_file, "wb") as f:
                                            f.write(image_data)
                                        logger.info(f"成功下载并保存头像: {wxid}")
                                        result["avatar_downloaded"] = True
                                    else:
                                        logger.warning(f"下载头像失败，状态码: {img_response.status}")

                            return result
                        else:
                            # 如果 /VXAPI 路径失败，尝试使用 /api 路径
                            logger.warning(f"使用849协议路径获取用户信息失败，尝试855协议路径")
                            raise Exception("使用849协议路径获取用户信息失败")
                    else:
                        # 如果 /VXAPI 路径失败，尝试使用 /api 路径
                        logger.warning(f"使用849协议路径获取用户信息失败，状态码: {response.status}，尝试855协议路径")
                        raise Exception(f"使用849协议路径获取用户信息失败，状态码: {response.status}")
            except Exception as e:
                # 尝试使用 /api 路径
                url = f'http://127.0.0.1:{api_port}/api/User/GetContractProfile?wxid={wxid}'
                logger.info(f"从API获取用户信息(855协议): {url}")

                async with session.post(url) as response:
                    if response.status == 200:
                        json_resp = await response.json()
                        logger.info(f"获取到用户信息响应(855协议): {json_resp.get('Success')}")

                        if json_resp.get("Success") and json_resp.get("Data"):
                            data = json_resp.get("Data")
                            result = {}

                            # 提取昵称
                            if data.get("userInfo") and data["userInfo"].get("NickName") and data["userInfo"]["NickName"].get("string"):
                                result["nickname"] = data["userInfo"]["NickName"]["string"]
                                logger.info(f"成功获取昵称: {result['nickname']}")

                            # 提取微信号
                            if data.get("userInfo") and data["userInfo"].get("Alias"):
                                result["alias"] = data["userInfo"]["Alias"]
                                logger.info(f"成功获取微信号: {result['alias']}")

                            # 提取头像 URL
                            if data.get("userInfoExt") and data["userInfoExt"].get("BigHeadImgUrl"):
                                head_img_url = data["userInfoExt"]["BigHeadImgUrl"]
                                logger.info(f"成功获取头像 URL: {head_img_url}")

                                # 下载头像
                                avatar_dir = Path("resource") / "avatars"
                                avatar_dir.mkdir(parents=True, exist_ok=True)
                                avatar_file = avatar_dir / f"{wxid}.jpg"

                                async with session.get(head_img_url) as img_response:
                                    if img_response.status == 200:
                                        image_data = await img_response.read()
                                        # 保存头像文件
                                        with open(avatar_file, "wb") as f:
                                            f.write(image_data)
                                        logger.info(f"成功下载并保存头像: {wxid}")
                                        result["avatar_downloaded"] = True
                                    else:
                                        logger.warning(f"下载头像失败，状态码: {img_response.status}")

                            return result
                        else:
                            logger.warning(f"获取用户信息失败: {json_resp}")
                    else:
                        logger.warning(f"请求失败，状态码: {response.status}")
    except Exception as e:
        logger.error(f"从API获取用户信息失败: {e}")

    return {}

async def download_avatar(wxid: str) -> bool:
    """下载微信头像

    Args:
        wxid: 微信ID

    Returns:
        bool: 是否成功下载头像
    """
    try:
        # 确保头像目录存在
        avatar_dir = Path("resource") / "avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)

        # 头像文件路径
        avatar_file = avatar_dir / f"{wxid}.jpg"

        # 如果头像文件已存在，直接返回
        if avatar_file.exists():
            logger.info(f"头像文件已存在: {wxid}")
            return True

        # 使用get_user_profile函数获取用户信息和头像
        user_info = await get_user_profile(wxid)
        if user_info and user_info.get("avatar_downloaded"):
            logger.info(f"通过get_user_profile成功下载头像: {wxid}")
            return True

        # 如果使用get_user_profile失败，尝试直接调用API
        try:
            import aiohttp

            # 直接调用API获取头像
            api_port = 9011  # 默认PAD API端口

            # 尝试使用两种API路径
            # 首先尝试 /VXAPI 路径 (849协议)
            url_849 = f'http://127.0.0.1:{api_port}/VXAPI/User/GetContractProfile?wxid={wxid}'
            # 然后尝试 /api 路径 (855协议)
            url_855 = f'http://127.0.0.1:{api_port}/api/User/GetContractProfile?wxid={wxid}'

            # 首先尝试849协议路径
            url = url_849
            logger.info(f"尝试直接调用API获取头像(849协议): {url}")

            async with aiohttp.ClientSession() as session:
                # 尝试使用849协议路径
                try:
                    async with session.post(url) as response:
                        if response.status == 200:
                            json_resp = await response.json()
                            logger.info(f"获取到用户信息响应(849协议): {json_resp.get('Success')}")

                            if json_resp.get("Success") and json_resp.get("Data"):
                                data = json_resp.get("Data")

                                if data.get("userInfoExt") and data["userInfoExt"].get("BigHeadImgUrl"):
                                    head_img_url = data["userInfoExt"]["BigHeadImgUrl"]
                                    logger.info(f"成功获取头像 URL: {head_img_url}")

                                    # 下载头像
                                    async with session.get(head_img_url) as img_response:
                                        if img_response.status == 200:
                                            image_data = await img_response.read()
                                            # 保存头像文件
                                            with open(avatar_file, "wb") as f:
                                                f.write(image_data)
                                            logger.info(f"成功下载并保存头像: {wxid}")
                                            return True
                                        else:
                                            logger.warning(f"下载头像失败，状态码: {img_response.status}")
                                else:
                                    logger.warning(f"响应中没有头像 URL")
                            else:
                                # 如果849协议路径失败，尝试855协议路径
                                logger.warning(f"使用849协议路径获取用户信息失败，尝试855协议路径")
                                raise Exception("使用849协议路径获取用户信息失败")
                        else:
                            # 如果849协议路径失败，尝试855协议路径
                            logger.warning(f"使用849协议路径获取用户信息失败，状态码: {response.status}，尝试855协议路径")
                            raise Exception(f"使用849协议路径获取用户信息失败，状态码: {response.status}")
                except Exception as e:
                    # 尝试使用855协议路径
                    url = url_855
                    logger.info(f"尝试直接调用API获取头像(855协议): {url}")

                    async with session.post(url) as response:
                        if response.status == 200:
                            json_resp = await response.json()
                            logger.info(f"获取到用户信息响应(855协议): {json_resp.get('Success')}")

                            if json_resp.get("Success") and json_resp.get("Data"):
                                data = json_resp.get("Data")

                                if data.get("userInfoExt") and data["userInfoExt"].get("BigHeadImgUrl"):
                                    head_img_url = data["userInfoExt"]["BigHeadImgUrl"]
                                    logger.info(f"成功获取头像 URL: {head_img_url}")

                                    # 下载头像
                                    async with session.get(head_img_url) as img_response:
                                        if img_response.status == 200:
                                            image_data = await img_response.read()
                                            # 保存头像文件
                                            with open(avatar_file, "wb") as f:
                                                f.write(image_data)
                                            logger.info(f"成功下载并保存头像: {wxid}")
                                            return True
                                        else:
                                            logger.warning(f"下载头像失败，状态码: {img_response.status}")
                                else:
                                    logger.warning(f"响应中没有头像 URL")
                            else:
                                logger.warning(f"获取用户信息失败: {json_resp}")
                        else:
                            logger.warning(f"请求失败，状态码: {response.status}")
        except Exception as e:
            logger.error(f"直接调用API获取头像失败: {e}")

        # 如果上述方法都失败，尝试使用默认头像
        try:
            # 尝试复制默认头像文件
            default_avatar_path = Path("admin") / "static" / "images" / "default_avatar.jpg"

            # 如果默认头像文件不存在，创建一个空文件
            if not default_avatar_path.exists():
                # 创建目录
                default_avatar_path.parent.mkdir(parents=True, exist_ok=True)

                # 创建一个简单的空文件
                with open(default_avatar_path, "wb") as f:
                    f.write(b"")
                logger.info(f"创建了默认头像文件: {default_avatar_path}")

            # 复制默认头像文件
            import shutil
            shutil.copy(default_avatar_path, avatar_file)
            logger.info(f"成功复制默认头像: {wxid}")
            return True
        except Exception as e:
            logger.error(f"使用默认头像失败: {e}")

            # 如果上面的方法都失败，创建一个空文件
            try:
                with open(avatar_file, "wb") as f:
                    f.write(b"")
                logger.info(f"创建了空头像文件: {wxid}")
                return True
            except Exception as e2:
                logger.error(f"创建空头像文件失败: {e2}")

        logger.info(f"已尝试下载头像: {wxid}")
        return False
    except Exception as e:
        logger.error(f"下载头像失败: {e}")
        return False

def get_bot_avatar(wxid: str) -> str:
    """获取微信机器人头像

    Args:
        wxid: 微信ID

    Returns:
        str: 头像的URL或空字符串
    """
    try:
        # 首先检查是否有本地头像文件
        avatar_dir = Path("resource") / "avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)

        avatar_file = avatar_dir / f"{wxid}.jpg"
        if avatar_file.exists():
            # 生成带随机参数的URL，避免浏览器缓存
            timestamp = int(time.time())
            # 返回相对路径
            return f"/api/accounts/avatar/{wxid}?t={timestamp}"

        # 如果没有本地头像，返回空字符串
        return ""
    except Exception as e:
        logger.error(f"获取头像失败: {e}")
        return ""

async def check_and_update_account_list():
    """检查并更新账号列表

    这个函数会检查当前活动账号，并将其信息保存到账号列表中。
    这样在登录成功后，账号管理页面就能立即显示账号信息。
    """
    try:
        # 获取当前账号
        current_account = get_current_account()
        if not current_account or not current_account.get("wxid"):
            logger.debug("当前没有活动账号，无需更新")
            return False

        # 尝试从API获取最新的用户信息
        wxid = current_account.get("wxid")
        user_info = await get_user_profile(wxid)

        # 如果成功获取到用户信息，更新当前账号
        if user_info:
            # 更新昵称和微信号
            if user_info.get("nickname"):
                current_account["nickname"] = user_info["nickname"]
            if user_info.get("alias"):
                current_account["alias"] = user_info["alias"]

            # 保存更新后的数据
            with open(ACTIVE_ACCOUNT_FILE, "w", encoding="utf-8") as f:
                json.dump(current_account, f, ensure_ascii=False, indent=2)
            logger.info(f"从API更新了账号信息: {current_account.get('nickname')}, {current_account.get('alias')}")

        # 保存当前账号到账号列表
        result = save_current_account_to_list()
        logger.info(f"保存当前账号到账号列表: {result}")
        return result
    except Exception as e:
        logger.error(f"检查并更新账号列表失败: {e}")
        return False

def register_account_manager_routes(app, auth_func, status_update_func=None, restart_func=None):
    """
    注册账号管理相关路由

    Args:
        app: FastAPI应用实例
        auth_func: 认证检查函数
        status_update_func: 更新机器人状态的函数
        restart_func: 重启系统的函数
    """
    global check_auth, update_bot_status, restart_system
    check_auth = auth_func
    update_bot_status = status_update_func
    restart_system = restart_func

    # 确保账号目录存在
    ensure_accounts_dir()

    # 注册路由
    app.include_router(router)

    # 添加检查和更新账号列表的API
    @app.get("/api/accounts/check-and-update")
    async def api_check_and_update_account_list(request: Request):
        """检查并更新账号列表API"""
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                logger.error("检查并更新账号列表失败：未认证")
                return JSONResponse(status_code=401, content={"success": False, "error": "未认证"})

            # 检查并更新账号列表
            result = await check_and_update_account_list()

            return JSONResponse(content={
                "success": True,
                "updated": result
            })
        except Exception as e:
            logger.error(f"检查并更新账号列表失败: {str(e)}")
            return JSONResponse(content={"success": False, "error": str(e)})

    # 添加头像获取API
    @app.get("/api/accounts/avatar/{wxid}")
    async def get_account_avatar(wxid: str):
        """获取账号头像"""
        avatar_dir = Path("resource") / "avatars"
        avatar_dir.mkdir(parents=True, exist_ok=True)
        avatar_file = avatar_dir / f"{wxid}.jpg"

        if not avatar_file.exists():
            # 如果没有头像，返回404
            logger.warning(f"头像文件不存在: {avatar_file}")
            return Response(status_code=404)

        # 检查文件大小
        file_size = os.path.getsize(avatar_file)
        logger.info(f"返回头像文件: {avatar_file}, 大小: {file_size} 字节")

        # 设置正确的内容类型和缓存控制
        return FileResponse(
            avatar_file,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

    # 添加账号管理页面路由
    @app.get("/accounts", response_class=HTMLResponse)
    async def accounts_page(request: Request):
        # 检查认证状态
        try:
            username = await check_auth(request)
            if not username:
                logger.debug("未找到会话 Cookie")
                return RedirectResponse(url="/login?next=/accounts")
            logger.debug(f"用户 {username} 访问账号管理页面")
        except Exception as e:
            logger.error(f"认证检查失败: {str(e)}")
            return RedirectResponse(url="/login?next=/accounts")

        # 获取版本信息
        from admin.server import get_version_info
        version_info = get_version_info()
        version = version_info.get("version", "1.0.0")
        update_available = version_info.get("update_available", False)
        latest_version = version_info.get("latest_version", "")
        update_url = version_info.get("update_url", "")
        update_description = version_info.get("update_description", "")

        # 创建Jinja2Templates实例
        templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "admin", "templates")
        templates = Jinja2Templates(directory=templates_dir)

        # 返回账号管理页面
        return templates.TemplateResponse(
            "accounts.html",
            {
                "request": request,
                "active_page": "accounts",
                "version": version,
                "update_available": update_available,
                "latest_version": latest_version,
                "update_url": update_url,
                "update_description": update_description
            }
        )

    logger.info("账号管理API路由注册成功")
