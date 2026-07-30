"""Celery Worker 专用数据库资源。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.setting import settings

# NullPool 避免 prefork 继承连接，也允许同步 Celery task 通过独立
# asyncio.run() 事件循环执行；每个任务仍创建独立 AsyncSession。
worker_async_engine = create_async_engine(
    settings.ASYNC_DB_URI,
    echo=settings.DATABASE_ECHO,
    pool_pre_ping=settings.POOL_PRE_PING,
    poolclass=NullPool,
)
worker_async_session = async_sessionmaker[AsyncSession](
    bind=worker_async_engine,
    expire_on_commit=False,
    autoflush=False,
)
