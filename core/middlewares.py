from typing import Any, Awaitable, Callable, Dict
from datetime import datetime
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from database.db import async_session
from database.models import Agent, User

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

# Эта Middleware достает настройки агента и ПРОВЕРЯЕТ ПОДПИСКУ
class AgentContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        agent_id = data.get("agent_id")
        
        if agent_id:
            session = data.get("session")
            if session:
                # 1. Используем joinedload, чтобы одним запросом достать и Агента, и его Владельца (User)
                result = await session.execute(
                    select(Agent).options(joinedload(Agent.owner)).where(Agent.id == agent_id)
                )
                agent = result.scalar_one_or_none()
                
                if agent:
                    owner = agent.owner
                    
                    # 2. ПРОВЕРКА СТАТУСА ПОДПИСКИ
                    # Если дата окончания подписки установлена и она меньше текущего времени (подписка истекла)
                    if owner.subscription_end_date and owner.subscription_end_date < datetime.utcnow():
                        
                        # Если это обычное текстовое сообщение, отвечаем заглушкой
                        if isinstance(event, Message):
                            await event.answer(
                                "⚠️ Извините, но этот бот временно недоступен.\n"
                                "Владельцу бота необходимо проверить статус своей подписки."
                            )
                        
                        # ВАЖНО: Прерываем выполнение!
                        # Мы НЕ вызываем await handler(event, data), 
                        # поэтому код не пойдет в handlers/agent.py и не потратит токены LLM.
                        return
                    
                    # 3. Если с подпиской всё в порядке, собираем конфиг и пускаем запрос дальше
                    data["agent_config"] = {
                        "id": agent.id,
                        "system_prompt": agent.system_prompt,
                        "is_active": agent.is_active,
                        "welcome_message": agent.welcome_message
                    }
        
        # Передаем управление в следующий хендлер (handlers/agent.py)
        return await handler(event, data)