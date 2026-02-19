import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Ваши импорты
from core.crypto import decrypt_token
from core.middlewares import AgentContextMiddleware, DbSessionMiddleware
from handlers.agent import agent_router 
from handlers.master import master_router 
from database.db import async_session, engine, Base 
from database.models import Agent
from core.config import settings
from qdrant_client import QdrantClient
from qdrant_client.http import models

# --- ГЕНЕРАТОР СЕССИЙ (Dependency Injection) ---
async def get_session():
    async with async_session() as session:
        yield session

# --- ЖИЗНЕННЫЙ ЦИКЛ ПРИЛОЖЕНИЯ ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ База данных инициализирована")

    client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))
    collection_name = "agent_documents"
    
    try:
        collections = client.get_collections().collections
        if not any(c.name == collection_name for c in collections):
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(size=384, distance=models.Distance.COSINE),
                sparse_vectors_config={
                    "sparse-text": models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=True)
                    )
                }
            )
            print(f"✅ Коллекция {collection_name} создана")
    except Exception as e:
        print(f"⚠️ Qdrant Error: {e}")

    webhook_url = f"{settings.BASE_URL}/webhook/master"
    await master_bot.set_webhook(url=webhook_url, drop_pending_updates=True)
    print(f"✅ Вебхук установлен")

    yield # Работа приложения

    # SHUTDOWN
    print("🛑 Закрытие ресурсов...")
    await master_dp.storage.close()
    await agent_dp.storage.close()
    await master_bot.session.close()

# --- ИНИЦИАЛИЗАЦИЯ APP (с передачей lifespan) ---
app = FastAPI(lifespan=lifespan)

# --- НАСТРОЙКА AIOGRAM ---
master_bot = Bot(token=settings.MASTER_BOT_TOKEN)
master_dp = Dispatcher(storage=MemoryStorage())
master_dp.update.middleware(DbSessionMiddleware(async_session)) 
master_dp.include_router(master_router)

agent_dp = Dispatcher(storage=MemoryStorage())
agent_dp.update.middleware(DbSessionMiddleware(async_session)) 
agent_dp.message.middleware(AgentContextMiddleware())
agent_dp.include_router(agent_router)

# --- ЭНДПОИНТЫ ---

@app.post("/webhook/master")
async def handle_master_webhook(request: Request):
    update_data = await request.json()
    tg_update = Update(**update_data)
    await master_dp.feed_update(master_bot, tg_update)
    return {"status": "ok"}

@app.post("/webhook/{bot_id}")
async def handle_agent_webhook(
    bot_id: int, 
    request: Request, 
    session: AsyncSession = Depends(get_session) # Внедрение сессии
):
    try:
        result = await session.execute(select(Agent).where(Agent.id == bot_id))
        agent = result.scalar_one_or_none()
        
        if not agent or not agent.is_active:
            return {"status": "ignored"}

        token = decrypt_token(agent.encrypted_token)
        
        # Используем контекстный менеджер бота для авто-закрытия сессии
        async with Bot(token=token) as bot:
            update_data = await request.json()
            tg_update = Update(**update_data)
            await agent_dp.feed_update(bot, tg_update, agent_id=agent.id, session=session)
            
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"❌ Ошибка в агенте {bot_id}: {e}")
        return {"status": "error"}