import asyncio
import logging
import tomllib
from datetime import datetime, timedelta
from typing import Optional, Union, List

from pydantic import validate_arguments
from sqlalchemy import Column, String, Text, DateTime, delete, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_scoped_session
from sqlalchemy.orm import declarative_base, sessionmaker

from utils.singleton import Singleton

DeclarativeBase = declarative_base()


class KeyValue(DeclarativeBase):
    __tablename__ = 'key_value_store'

    key = Column(String(255), primary_key=True, unique=True, comment='键名')
    value = Column(Text, nullable=False, comment='存储值')
    expire_time = Column(DateTime, index=True, comment='过期时间')


class KeyvalDB(metaclass=Singleton):
    _instance = None

    def __new__(cls):
        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)
        db_url = main_config["XYBot"]["keyvalDB-url"]

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
        # 启动后台清理任务
        asyncio.create_task(self._cleanup_expired())

    @validate_arguments
    async def set(
            self,
            key: str,
            value: Union[str, dict, list],
            ex: Optional[Union[int, timedelta]] = None
    ) -> bool:
        """设置键值对，支持过期时间（秒或timedelta）"""
        async with self._async_session_factory() as session:
            try:
                expire_time = None
                if ex:
                    # 统一处理时间类型转换
                    if isinstance(ex, int):
                        expire_time = datetime.now() + timedelta(seconds=ex)
                    else:
                        expire_time = datetime.now() + ex

                kv = KeyValue(
                    key=key,
                    value=str(value),
                    expire_time=expire_time
                )
                await session.merge(kv)
                await session.commit()
                return True
            except Exception as e:
                logging.error(f"设置键值失败: {str(e)}")
                await session.rollback()
                return False

    async def get(self, key: str) -> Optional[str]:
        """获取键值，自动处理过期数据"""
        async with self._async_session_factory() as session:
            result = await session.get(KeyValue, key)
            if not result:
                return None

            if result.expire_time and result.expire_time < datetime.now():
                await session.delete(result)
                await session.commit()
                return None

            return result.value

    async def delete(self, key: str) -> bool:
        """删除键值"""
        async with self._async_session_factory() as session:
            result = await session.execute(delete(KeyValue).where(KeyValue.key == key))
            await session.commit()
            return result.rowcount > 0

    async def exists(self, key: str) -> bool:
        """检查键是否存在"""
        async with self._async_session_factory() as session:
            result = await session.get(KeyValue, key)
            if result and result.expire_time and result.expire_time < datetime.now():
                await session.delete(result)
                await session.commit()
                return False
            return result is not None

    async def ttl(self, key: str) -> int:
        """获取剩余生存时间（秒）"""
        async with self._async_session_factory() as session:
            result = await session.get(KeyValue, key)
            if not result or not result.expire_time:
                return -1

            remaining = (result.expire_time - datetime.now()).total_seconds()
            # 明确返回类型处理
            return int(remaining) if remaining > 0 else -2

    async def expire(self, key: str, ex: Union[int, timedelta]) -> bool:
        """设置过期时间"""
        async with self._async_session_factory() as session:
            result = await session.get(KeyValue, key)
            if not result:
                return False

            expire_time = datetime.now() + (ex if isinstance(ex, timedelta) else timedelta(seconds=ex))
            result.expire_time = expire_time
            await session.commit()
            return True

    async def keys(self, pattern: str = "*") -> List[str]:
        """查找匹配模式的键"""
        async with self._async_session_factory() as session:
            # 显式指定查询列类型
            query = select(KeyValue.key).where(KeyValue.key.like(pattern.replace("*", "%")))
            result = await session.execute(query)
            return [str(row[0]) for row in result.all()]  # 确保返回字符串类型

    async def _cleanup_expired(self, interval: int = 3600):
        """后台定时清理过期数据"""
        while True:
            async with self._async_session_factory() as session:
                await session.execute(
                    delete(KeyValue).where(KeyValue.expire_time < datetime.now())
                )
                await session.commit()
            await asyncio.sleep(interval)

    async def close(self):
        """关闭数据库连接"""
        await self.engine.dispose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
