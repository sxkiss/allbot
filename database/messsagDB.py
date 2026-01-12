import asyncio
import logging
import tomllib
from datetime import datetime, timedelta
from typing import Optional, List

from pydantic import validate_arguments
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_scoped_session
from sqlalchemy.orm import declarative_base, sessionmaker

from utils.singleton import Singleton

# 使用新的声明式基类
DeclarativeBase = declarative_base()


class Message(DeclarativeBase):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    msg_id = Column(Integer, index=True, comment='消息唯一ID（整型）')
    sender_wxid = Column(String(40), index=True, comment='消息发送人wxid')
    from_wxid = Column(String(40), index=True, comment='消息来源wxid')
    msg_type = Column(Integer, comment='消息类型（整型编码）')
    content = Column(Text, comment='消息内容')
    timestamp = Column(DateTime, default=datetime.now, index=True, comment='消息时间戳')
    is_group = Column(Boolean, default=False, comment='是否群消息')


class MessageDB(metaclass=Singleton):
    _instance = None

    def __new__(cls):
        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)
        db_url = main_config["XYBot"]["msgDB-url"]

        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.engine = create_async_engine(
                db_url,
                echo=False,
                future=True
            )
            cls._async_session_factory = async_scoped_session(
                sessionmaker(
                    cls._instance.engine,
                    class_=AsyncSession,
                    expire_on_commit=False
                ),
                scopefunc=asyncio.current_task
            )
        return cls._instance

    async def initialize(self):
        """异步初始化数据库"""
        async with self.engine.begin() as conn:
            await conn.run_sync(DeclarativeBase.metadata.create_all)

    @validate_arguments(config=dict(arbitrary_types_allowed=True))
    async def save_message(self,
                           msg_id: int,
                           sender_wxid: str = "",
                           from_wxid: str = "",
                           msg_type: int = 0,
                           content: str = "",
                           is_group: bool = False) -> bool:
        """异步保存消息到数据库"""
        # 确保content是字符串类型
        if isinstance(content, dict) and "string" in content:
            content = content["string"]
        elif not isinstance(content, str):
            content = str(content)
            
        async with self._async_session_factory() as session:
            try:
                message = Message(
                    msg_id=msg_id,
                    sender_wxid=sender_wxid,
                    from_wxid=from_wxid,
                    msg_type=msg_type,
                    content=content,
                    is_group=is_group,
                    timestamp=datetime.now()
                )
                session.add(message)
                await session.commit()
                return True
            except Exception as e:
                logging.error(f"保存消息失败: {str(e)}")
                await session.rollback()
                return False

    async def get_messages(self,
                           start_time: Optional[datetime] = None,
                           end_time: Optional[datetime] = None,
                           sender_wxid: Optional[str] = None,
                           from_wxid: Optional[str] = None,
                           msg_type: Optional[int] = None,
                           is_group: Optional[bool] = None,
                           limit: int = 100) -> List[Message]:
        """异步查询消息记录"""
        async with self._async_session_factory() as session:
            try:
                query = select(Message).order_by(Message.timestamp.desc()).limit(limit)

                if start_time:
                    query = query.where(Message.timestamp >= start_time)
                if end_time:
                    query = query.where(Message.timestamp <= end_time)
                if sender_wxid:
                    query = query.where(Message.sender_wxid == sender_wxid)
                if from_wxid:
                    query = query.where(Message.from_wxid == from_wxid)
                if msg_type is not None:
                    query = query.where(Message.msg_type == msg_type)
                if is_group is not None:
                    query = query.where(Message.is_group == is_group)

                result = await session.execute(query)
                return result.scalars().all()
            except Exception as e:
                logging.error(f"查询消息失败: {str(e)}")
                return []

    async def close(self):
        """关闭数据库连接"""
        await self.engine.dispose()

    async def cleanup_messages(self):
        """每三天清理旧消息"""
        while True:
            async with self._async_session_factory() as session:
                try:
                    # 计算三天前的时间
                    three_days_ago = datetime.now() - timedelta(days=3)
                    # 删除三天前的消息
                    await session.execute(
                        delete(Message).where(Message.timestamp < three_days_ago)
                    )
                    await session.commit()
                except Exception as e:
                    logging.error(f"清理消息失败: {str(e)}")
                    await session.rollback()
            await asyncio.sleep(259200)  # 每三天（259200秒）执行一次

    async def __aenter__(self):
        # 启动清理消息的定时任务
        asyncio.create_task(self.cleanup_messages())
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
