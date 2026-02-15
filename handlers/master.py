import os
import asyncio
from aiogram import Router, F, Bot, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from database.models import User, Agent, AgentDocument
from core.crypto import encrypt_token
from services.indexer import process_document
from states.master import CreateAgentSG
from keyboards.master_kb import get_main_menu

master_router = Router()

# --- Вспомогательная функция для безопасности Markdown ---
def escape_md(text: str) -> str:
    """Экранирует нижнее подчеркивание для стандартного Markdown."""
    if not text:
        return ""
    return text.replace("_", "\\_")

# --- ГЛАВНОЕ МЕНЮ ---

@master_router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession):
    res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = res.scalar_one_or_none()
    if not user:
        user = User(telegram_id=message.from_user.id, username=message.from_user.username)
        session.add(user)
        await session.commit()
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! Это конструктор AI-агентов.", 
        reply_markup=get_main_menu()
    )

@master_router.callback_query(F.data == "start_menu")
async def back_to_menu(callback: types.CallbackQuery, session: AsyncSession):
    await callback.message.delete()
    await cmd_start(callback.message, session)

# --- ПРОФИЛЬ (ЗДЕСЬ БЫЛА ОШИБКА) ---

@master_router.callback_query(F.data == "profile")
async def show_profile(callback: types.CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id
    
    user_res = await session.execute(select(User).where(User.telegram_id == tg_id))
    user = user_res.scalar_one_or_none()
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден.")
        return

    query_count = select(func.count(Agent.id)).where(Agent.owner_id == user.id)
    result_count = await session.execute(query_count)
    agents_count = result_count.scalar()

    query_agents = select(Agent.bot_username).where(Agent.owner_id == user.id).limit(5)
    result_agents = await session.execute(query_agents)
    agents_names = result_agents.scalars().all()

    # Экранируем юзернеймы ботов, чтобы подчеркивания не ломали Markdown
    agents_list_str = "\n".join([f"• @{escape_md(name)}" for name in agents_names if name]) \
        if agents_names else "У вас пока нет агентов."
    
    profile_text = (
        "👤 *Мой профиль*\n\n"
        f"🆔 Ваш ID: `{tg_id}`\n"
        f"🤖 Создано агентов: {agents_count}\n\n"
        "*Ваши последние боты:*\n"
        f"{agents_list_str}\n\n"
        "💡 Здесь можно управлять подпиской."
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="start_menu")]
    ])

    try:
        await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode="Markdown")
    except Exception as e:
        # Если Markdown всё равно упадет, отправляем чистым текстом
        print(f"❌ Ошибка парсинга Markdown: {e}")
        await callback.message.edit_text(profile_text.replace("*", "").replace("`", ""), reply_markup=kb)

# --- СОЗДАНИЕ АГЕНТА ---

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
        await message.answer(f"✅ Бот @{escape_md(bot_info.username)} успешно подключен!\nТеперь напиши системный промпт:")
        await state.set_state(CreateAgentSG.waiting_prompt)

    except Exception as e:
        await message.answer(f"❌ Ошибка токена: {e}")

@master_router.message(CreateAgentSG.waiting_prompt)
async def process_prompt(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    agent_id = data['agent_id']
    await session.execute(update(Agent).where(Agent.id == agent_id).values(system_prompt=message.text))
    await session.commit()
    await message.answer("Отправь файлы (.pdf, .docx, .txt). Когда закончишь, нажми /start")
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
    await message.answer(f"⏳ Файл '{escape_md(file_name)}' принят.")

@master_router.message(CreateAgentSG.waiting_docs, CommandStart())
async def finish_setup(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.clear()
    await cmd_start(message, session)