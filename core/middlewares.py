from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy import select
from database.db import async_session
from database.models import Agent

# Эта Middleware создает сессию БД и передает её в хендлер как аргумент "session"
class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool):
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            return await handler(event, data)

# Эта Middleware достает настройки агента
class AgentContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        agent_id = data.get("agent_id")
        
        if agent_id:
            # Используем уже имеющуюся сессию из DbSessionMiddleware (если она есть)
            # или создаем новую
            session = data.get("session")
            if session:
                result = await session.execute(
                    select(Agent).where(Agent.id == agent_id)
                )
                agent = result.scalar_one_or_none()
                if agent:
                    data["agent_config"] = {
                        "id": agent.id,
                        "system_prompt": agent.system_prompt,
                        "is_active": agent.is_active,
                        "welcome_message": agent.welcome_message
                    }
        
        return await handler(event, data)