import os
import json
import time
import sqlite3
from datetime import datetime
from loguru import logger

# 数据库文件路径
DB_PATH = os.path.join("database", "contacts.db")

def ensure_db_dir():
    """确保数据库目录存在"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def create_group_members_table():
    """创建群成员表"""
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 创建群成员表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_wxid TEXT NOT NULL,
        member_wxid TEXT NOT NULL,
        nickname TEXT,
        display_name TEXT,
        avatar TEXT,
        inviter_wxid TEXT,
        join_time INTEGER,
        last_updated INTEGER,
        extra_data TEXT,
        UNIQUE(group_wxid, member_wxid)
    )
    ''')

    # 创建索引以加快查询速度
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_group_wxid ON group_members (group_wxid)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_member_wxid ON group_members (member_wxid)')

    conn.commit()
    conn.close()
    logger.info("群成员数据表创建完成")

def save_group_members_to_db(group_wxid, members):
    """保存群成员列表到数据库

    Args:
        group_wxid: 群聊的wxid
        members: 群成员列表
    
    Returns:
        bool: 是否成功保存
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 创建表（如果不存在）
        create_group_members_table()

        # 准备批量插入
        current_time = int(time.time())
        for member in members:
            # 提取基本字段
            member_wxid = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
            if not member_wxid:
                logger.warning(f"跳过没有wxid的群成员: {member}")
                continue

            # 处理昵称字段
            nickname = None
            if member.get("NickName"):
                nickname = member.get("NickName")
            elif member.get("nickname"):
                nickname = member.get("nickname")

            # 处理显示名字段
            display_name = None
            if member.get("DisplayName"):
                display_name = member.get("DisplayName")
            elif member.get("display_name"):
                display_name = member.get("display_name")

            # 处理头像字段
            avatar = None
            if member.get("BigHeadImgUrl"):
                avatar = member.get("BigHeadImgUrl")
            elif member.get("SmallHeadImgUrl"):
                avatar = member.get("SmallHeadImgUrl")
            elif member.get("avatar"):
                avatar = member.get("avatar")
            elif member.get("HeadImgUrl"):
                avatar = member.get("HeadImgUrl")

            # 处理邀请人字段
            inviter_wxid = member.get("InviterUserName") or ""

            # 将其他字段存储为JSON
            extra_data = {}
            for key, value in member.items():
                if key not in ["wxid", "Wxid", "UserName", "NickName", "nickname", "DisplayName", "display_name", 
                              "BigHeadImgUrl", "SmallHeadImgUrl", "avatar", "HeadImgUrl", "InviterUserName"]:
                    extra_data[key] = value

            extra_data_json = json.dumps(extra_data, ensure_ascii=False)

            # 插入或更新群成员
            cursor.execute('''
            INSERT OR REPLACE INTO group_members
            (group_wxid, member_wxid, nickname, display_name, avatar, inviter_wxid, last_updated, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                group_wxid,
                member_wxid,
                nickname,
                display_name,
                avatar,
                inviter_wxid,
                current_time,
                extra_data_json
            ))

        conn.commit()
        conn.close()
        logger.success(f"成功保存群 {group_wxid} 的 {len(members)} 个成员到数据库")
        return True
    except Exception as e:
        logger.error(f"保存群成员到数据库失败: {str(e)}")
        return False

def get_group_members_from_db(group_wxid):
    """从数据库获取群成员列表

    Args:
        group_wxid: 群聊的wxid

    Returns:
        list: 群成员列表
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询群成员
        cursor.execute('''
        SELECT member_wxid, nickname, display_name, avatar, inviter_wxid, join_time, last_updated, extra_data
        FROM group_members
        WHERE group_wxid = ?
        ORDER BY nickname COLLATE NOCASE
        ''', (group_wxid,))
        
        rows = cursor.fetchall()
        members = []
        
        for row in rows:
            member = {
                "wxid": row[0],
                "nickname": row[1] or "",
                "display_name": row[2] or "",
                "avatar": row[3] or "",
                "inviter_wxid": row[4] or "",
                "join_time": row[5] or 0,
                "last_updated": row[6] or 0
            }
            
            # 解析额外数据
            if row[7]:
                try:
                    extra_data = json.loads(row[7])
                    for key, value in extra_data.items():
                        member[key] = value
                except:
                    pass
            
            members.append(member)
        
        conn.close()
        logger.info(f"从数据库加载了群 {group_wxid} 的 {len(members)} 个成员")
        return members
    except Exception as e:
        logger.error(f"从数据库获取群 {group_wxid} 的成员失败: {str(e)}")
        return []

def get_group_member_from_db(group_wxid, member_wxid):
    """从数据库获取单个群成员信息

    Args:
        group_wxid: 群聊的wxid
        member_wxid: 成员的wxid

    Returns:
        dict: 成员信息，如果不存在则返回None
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询群成员
        cursor.execute('''
        SELECT member_wxid, nickname, display_name, avatar, inviter_wxid, join_time, last_updated, extra_data
        FROM group_members
        WHERE group_wxid = ? AND member_wxid = ?
        ''', (group_wxid, member_wxid))
        
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        
        member = {
            "wxid": row[0],
            "nickname": row[1] or "",
            "display_name": row[2] or "",
            "avatar": row[3] or "",
            "inviter_wxid": row[4] or "",
            "join_time": row[5] or 0,
            "last_updated": row[6] or 0
        }
        
        # 解析额外数据
        if row[7]:
            try:
                extra_data = json.loads(row[7])
                for key, value in extra_data.items():
                    member[key] = value
            except:
                pass
        
        conn.close()
        return member
    except Exception as e:
        logger.error(f"从数据库获取群 {group_wxid} 的成员 {member_wxid} 失败: {str(e)}")
        return None

def update_group_member_in_db(group_wxid, member):
    """更新单个群成员信息

    Args:
        group_wxid: 群聊的wxid
        member: 成员信息

    Returns:
        bool: 是否成功更新
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 创建表（如果不存在）
        create_group_members_table()

        # 提取基本字段
        member_wxid = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
        if not member_wxid:
            logger.error("更新群成员失败: 缺少wxid")
            return False

        # 处理昵称字段
        nickname = None
        if member.get("NickName"):
            nickname = member.get("NickName")
        elif member.get("nickname"):
            nickname = member.get("nickname")

        # 处理显示名字段
        display_name = None
        if member.get("DisplayName"):
            display_name = member.get("DisplayName")
        elif member.get("display_name"):
            display_name = member.get("display_name")

        # 处理头像字段
        avatar = None
        if member.get("BigHeadImgUrl"):
            avatar = member.get("BigHeadImgUrl")
        elif member.get("SmallHeadImgUrl"):
            avatar = member.get("SmallHeadImgUrl")
        elif member.get("avatar"):
            avatar = member.get("avatar")
        elif member.get("HeadImgUrl"):
            avatar = member.get("HeadImgUrl")

        # 处理邀请人字段
        inviter_wxid = member.get("InviterUserName") or ""

        # 将其他字段存储为JSON
        extra_data = {}
        for key, value in member.items():
            if key not in ["wxid", "Wxid", "UserName", "NickName", "nickname", "DisplayName", "display_name", 
                          "BigHeadImgUrl", "SmallHeadImgUrl", "avatar", "HeadImgUrl", "InviterUserName"]:
                extra_data[key] = value

        extra_data_json = json.dumps(extra_data, ensure_ascii=False)
        current_time = int(time.time())

        # 插入或更新群成员
        cursor.execute('''
        INSERT OR REPLACE INTO group_members
        (group_wxid, member_wxid, nickname, display_name, avatar, inviter_wxid, last_updated, extra_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            group_wxid,
            member_wxid,
            nickname,
            display_name,
            avatar,
            inviter_wxid,
            current_time,
            extra_data_json
        ))

        conn.commit()
        conn.close()
        logger.info(f"成功更新群 {group_wxid} 的成员 {member_wxid}")
        return True
    except Exception as e:
        logger.error(f"更新群 {group_wxid} 的成员 {member_wxid} 失败: {str(e)}")
        return False

def delete_group_member_from_db(group_wxid, member_wxid):
    """从数据库删除群成员

    Args:
        group_wxid: 群聊的wxid
        member_wxid: 成员的wxid

    Returns:
        bool: 是否成功删除
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 删除群成员
        cursor.execute('''
        DELETE FROM group_members
        WHERE group_wxid = ? AND member_wxid = ?
        ''', (group_wxid, member_wxid))

        conn.commit()
        conn.close()
        logger.info(f"从数据库删除群 {group_wxid} 的成员 {member_wxid}")
        return True
    except Exception as e:
        logger.error(f"从数据库删除群 {group_wxid} 的成员 {member_wxid} 失败: {str(e)}")
        return False

def delete_all_group_members(group_wxid):
    """删除群的所有成员记录

    Args:
        group_wxid: 群聊的wxid

    Returns:
        bool: 是否成功删除
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 删除群所有成员
        cursor.execute('DELETE FROM group_members WHERE group_wxid = ?', (group_wxid,))

        conn.commit()
        conn.close()
        logger.info(f"从数据库删除群 {group_wxid} 的所有成员")
        return True
    except Exception as e:
        logger.error(f"从数据库删除群 {group_wxid} 的所有成员失败: {str(e)}")
        return False

def get_member_groups(member_wxid):
    """获取成员所在的所有群

    Args:
        member_wxid: 成员的wxid

    Returns:
        list: 群wxid列表
    """
    ensure_db_dir()
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 查询成员所在的群
        cursor.execute('''
        SELECT DISTINCT group_wxid
        FROM group_members
        WHERE member_wxid = ?
        ''', (member_wxid,))
        
        rows = cursor.fetchall()
        groups = [row[0] for row in rows]
        
        conn.close()
        return groups
    except Exception as e:
        logger.error(f"获取成员 {member_wxid} 所在的群失败: {str(e)}")
        return []

# 初始化数据库
def init_db():
    """初始化数据库"""
    create_group_members_table()
    logger.info("群成员数据库初始化完成")

# 当模块被导入时自动初始化数据库
init_db()
