import os
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

from core.crypto import decrypt_token
from core.middlewares import AgentContextMiddleware, DbSessionMiddleware
from handlers.agent import agent_router 
from handlers.master import master_router 
from database.db import async_session, engine, Base 
from sqlalchemy import select
from database.models import Agent
from core.config import settings

app = FastAPI()

# --- 1. Настройка Мастер-бота (Конструктора) ---
master_bot = Bot(token=settings.MASTER_BOT_TOKEN)
master_dp = Dispatcher(storage=MemoryStorage())

# Подключаем сессию БД к Мастеру (решает проблему missing positional argument 'session')
master_dp.update.middleware(DbSessionMiddleware(async_session)) 
master_dp.include_router(master_router)

# --- 2. Настройка ботов-агентов ---
agent_dp = Dispatcher(storage=MemoryStorage())

# Агентам нужна и сессия БД, и контекст самого агента
agent_dp.update.middleware(DbSessionMiddleware(async_session)) 
agent_dp.message.middleware(AgentContextMiddleware())
agent_dp.include_router(agent_router)

# --- События старта и остановки FastAPI ---
@app.on_event("startup")
async def on_startup():
    # Автоматическое создание таблиц в БД
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ База данных инициализирована")

    # Автоматическая установка вебхука для Мастер-бота
    webhook_url = f"{settings.BASE_URL}/webhook/master"
    await master_bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    print(f"✅ Вебхук Мастер-бота установлен на: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await master_dp.storage.close()
    await agent_dp.storage.close()
    await master_bot.session.close()

# --- Эндпоинт для МАСТЕР-БОТА ---
@app.post("/webhook/master")
async def handle_master_webhook(request: Request):
    try:
        update_data = await request.json()
        tg_update = Update(**update_data)
        await master_dp.feed_update(master_bot, tg_update)
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Ошибка в мастере: {e}")
        return {"status": "error", "message": str(e)}

# --- Эндпоинт для БОТОВ-АГЕНТОВ ---
@app.post("/webhook/{bot_id}")
async def handle_agent_webhook(bot_id: int, request: Request):
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Agent).where(Agent.id == bot_id)
            )
            agent = result.scalar_one_or_none()
            
            if not agent or not agent.is_active:
                return {"status": "ignored"}

            # Дешифруем токен и создаем объект бота
            token = decrypt_token(agent.encrypted_token)
            bot = Bot(token=token)
            
            update_data = await request.json()
            tg_update = Update(**update_data)
            
            # Передаем agent_id, чтобы AgentContextMiddleware мог найти настройки в БД
            await agent_dp.feed_update(bot, tg_update, agent_id=agent.id)
            
            # Важно: закрываем сессию бота после обработки, чтобы не висели в памяти
            await bot.session.close()
            return {"status": "ok"}
    except Exception as e:
        print(f"❌ Ошибка в агенте {bot_id}: {e}")
        return {"status": "error"}