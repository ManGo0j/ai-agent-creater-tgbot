import os
import asyncio
from aiogram import Router, F, Bot, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import User, Agent, AgentDocument
from core.crypto import encrypt_token
from services.indexer import process_document
from states.master import CreateAgentSG
from keyboards.master_kb import get_main_menu

master_router = Router()

@master_router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession):
    res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        user = User(telegram_id=message.from_user.id, username=message.from_user.username)
        session.add(user)
        await session.commit()
    
    await message.answer(f"Привет, {message.from_user.first_name}! Это конструктор AI-агентов.", 
                         reply_markup=get_main_menu())

@master_router.callback_query(F.data == "add_agent")
async def start_add_agent(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправь API токен нового бота из @BotFather:")
    await state.set_state(CreateAgentSG.waiting_token)

@master_router.message(CreateAgentSG.waiting_token)
async def process_token(message: types.Message, state: FSMContext, session: AsyncSession):
    token = message.text.strip()
    try:
        temp_bot = Bot(token=token)
        bot_info = await temp_bot.get_me()
        
        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar()
        
        new_agent = Agent(
            owner_id=user.id,
            encrypted_token=encrypt_token(token),
            bot_username=bot_info.username
        )
        session.add(new_agent)
        await session.commit()

        webhook_url = f"{os.getenv('BASE_URL')}/webhook/{new_agent.id}"
        await temp_bot.set_webhook(url=webhook_url)
        await temp_bot.session.close()

        await state.update_data(agent_id=new_agent.id)
        await message.answer(f"✅ Бот @{bot_info.username} успешно подключен!\nТеперь напиши системный промпт (роль бота):")
        await state.set_state(CreateAgentSG.waiting_prompt)

    except Exception as e:
        await message.answer(f"❌ Ошибка токена или подключения: {e}\nПопробуй еще раз.")

@master_router.message(CreateAgentSG.waiting_prompt)
async def process_prompt(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    agent_id = data['agent_id']
    
    from sqlalchemy import update
    await session.execute(
        update(Agent).where(Agent.id == agent_id).values(system_prompt=message.text)
    )
    await session.commit()
    
    await message.answer("Отлично! Теперь отправь мне файлы (.pdf, .docx, .txt) для обучения бота. Когда закончишь, просто напиши /done")
    await state.set_state(CreateAgentSG.waiting_docs)

@master_router.message(CreateAgentSG.waiting_docs, F.document)
async def handle_docs(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    agent_id = data['agent_id']
    
    file_id = message.document.file_id
    file_name = message.document.file_name
    
    new_doc = AgentDocument(agent_id=agent_id, file_name=file_name, file_id=file_id, status="processing")
    session.add(new_doc)
    await session.commit()
    
    os.makedirs("temp_uploads", exist_ok=True)
    file_path = f"temp_uploads/{file_id}_{file_name}"
    await bot.download(message.document, destination=file_path)
    
    asyncio.create_task(process_document(file_path, agent_id, new_doc.id))
    await message.answer(f"⏳ Файл '{file_name}' принят в обработку. Я уведомлю, когда бот его 'прочитает'.")

@master_router.message(CreateAgentSG.waiting_docs, CommandStart())
async def finish_setup(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Настройка завершена! Твой бот готов к работе.", reply_markup=get_main_menu())