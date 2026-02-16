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
from aiogram.utils.keyboard import InlineKeyboardBuilder
from core.crypto import decrypt_token  
from services.search_service import delete_agent_vectors

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
        
        # --- ПРОВЕРКА ПО УНИКАЛЬНОМУ ID БОТА ---
        # Это защитит от смены username
        existing_agent_res = await session.execute(
            select(Agent).where(Agent.bot_id == bot_info.id)
        )
        existing_agent = existing_agent_res.scalar_one_or_none()

        if existing_agent:
            await temp_bot.session.close()
            return await message.answer(
                f"❌ Этот бот (ID: {bot_info.id}) уже зарегистрирован в системе под юзернеймом @{escape_md(existing_agent.bot_username)}.\n"
                "Один и тот же бот не может быть добавлен дважды."
            )
        # ---------------------------------------

        user_res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
        user = user_res.scalar()
        
        new_agent = Agent(
            owner_id=user.id,
            bot_id=bot_info.id, # Сохраняем неизменный ID
            encrypted_token=encrypt_token(token),
            bot_username=bot_info.username # Сохраняем для красоты в меню
        )
        session.add(new_agent)
        await session.commit()

        # Ставим вебхук с очисткой очереди
        await temp_bot.set_webhook(
            url=f"{os.getenv('BASE_URL')}/webhook/{new_agent.id}",
            drop_pending_updates=True
        )
        await temp_bot.session.close()

        await state.update_data(agent_id=new_agent.id)
        await message.answer(f"✅ Бот @{escape_md(bot_info.username)} успешно подключен!\nТеперь напиши системный промпт:")
        await state.set_state(CreateAgentSG.waiting_prompt)

    except Exception as e:
        if 'temp_bot' in locals(): await temp_bot.session.close()
        await message.answer(f"❌ Ошибка: {e}")

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

# --- МОИ АГЕНТЫ (СПИСОК) ---

@master_router.callback_query(F.data == "my_agents")
async def show_my_agents(callback: types.CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id
    
    # Получаем внутренний ID пользователя
    user_res = await session.execute(select(User.id).where(User.telegram_id == tg_id))
    user_id = user_res.scalar_one_or_none()
    
    if not user_id:
        await callback.answer("Ошибка: пользователь не найден.", show_alert=True)
        return

    # Достаем всех агентов этого пользователя
    agents_res = await session.execute(select(Agent).where(Agent.owner_id == user_id))
    agents = agents_res.scalars().all()

    # Если агентов нет
    if not agents:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="➕ Создать агента", callback_data="add_agent")],
            [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="start_menu")]
        ])
        await callback.message.edit_text(" У вас пока нет созданных ботов.\nСамое время создать первого!", reply_markup=kb)
        return

    # Если агенты есть, собираем клавиатуру через Builder
    builder = InlineKeyboardBuilder()
    for agent in agents:
        # Название кнопки: юзернейм или просто ID
        bot_name = f"@{agent.bot_username}" if agent.bot_username else f"Агент #{agent.id}"
        # В callback_data зашиваем ID конкретного агента
        builder.button(text=bot_name, callback_data=f"agent_info_{agent.id}")
    
    # Делаем по 1 кнопке в ряд
    builder.adjust(1)
    # Добавляем кнопку возврата в конце
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="start_menu"))

    await callback.message.edit_text(
        "🤖 *Ваши агенты:*\nВыберите бота для просмотра подробной информации:", 
        reply_markup=builder.as_markup(), 
        parse_mode="Markdown"
    )

# --- ИНФОРМАЦИЯ О КОНКРЕТНОМ АГЕНТЕ ---

@master_router.callback_query(F.data.startswith("agent_info_"))
async def show_agent_info(callback: types.CallbackQuery, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])
    
    agent_res = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_res.scalar_one_or_none()
    
    if not agent:
        await callback.answer("Агент не найден.", show_alert=True)
        return

    docs_res = await session.execute(
        select(func.count(AgentDocument.id)).where(AgentDocument.agent_id == agent_id)
    )
    docs_count = docs_res.scalar()

    bot_name = escape_md(agent.bot_username) if agent.bot_username else "Бот"
    status_text = "✅ Активен" if agent.is_active else "❌ Отключен"
    toggle_label = "🔴 Отключить" if agent.is_active else "🟢 Включить"
    
    text = (
        f"🤖 *Управление агентом*\n\n"
        f"🔗 *Бот:* @{bot_name}\n"
        f"📊 *Статус:* {status_text}\n"
        f"📚 *Документов:* {docs_count}\n\n"
        f"🧠 *Промпт:* \n_{escape_md(agent.system_prompt[:200])}..._"
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text=toggle_label, callback_data=f"toggle_agent_{agent_id}"),
            types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_{agent_id}")
        ],
        [types.InlineKeyboardButton(text="⬅️ К списку агентов", callback_data="my_agents")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# --- ПЕРЕКЛЮЧЕНИЕ СТАТУСА ---

@master_router.callback_query(F.data.startswith("toggle_agent_"))
async def toggle_agent(callback: types.CallbackQuery, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])
    agent = await session.get(Agent, agent_id)

    if not agent:
        return await callback.answer("Агент не найден.")

    # Переключаем состояние в БД
    new_status = not agent.is_active
    agent.is_active = new_status
    await session.commit()

    try:
        from core.crypto import decrypt_token
        temp_bot = Bot(token=decrypt_token(agent.encrypted_token))
        
        if new_status:
            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            # Добавляем drop_pending_updates=True, чтобы удалить старые сообщения
            webhook_url = f"{os.getenv('BASE_URL')}/webhook/{agent.id}"
            await temp_bot.set_webhook(
                url=webhook_url, 
                drop_pending_updates=True  # Игнорировать всё, что прислали, пока бот был выключен
            )
        else:
            # При отключении просто удаляем вебхук
            await temp_bot.delete_webhook()
            
        await temp_bot.session.close()
    except Exception as e:
        print(f"Ошибка вебхука при переключении: {e}")

    await callback.answer(f"Статус изменен: {'Включен' if new_status else 'Отключен'}")
    await show_agent_info(callback, session)

# --- УДАЛЕНИЕ АГЕНТА ---

@master_router.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete(callback: types.CallbackQuery):
    agent_id = callback.data.split("_")[2]
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="❌ ДА, УДАЛИТЬ", callback_data=f"delete_force_{agent_id}"),
            types.InlineKeyboardButton(text="✅ ОТМЕНА", callback_data=f"agent_info_{agent_id}")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ *ВНИМАНИЕ!*\nВы уверены, что хотите удалить этого агента? Все данные и привязка бота будут стерты.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@master_router.callback_query(F.data.startswith("delete_force_"))
async def delete_agent(callback: types.CallbackQuery, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])
    agent = await session.get(Agent, agent_id)

    if agent:
        try:
            # 1. Отключаем вебхук перед удалением
            temp_bot = Bot(token=decrypt_token(agent.encrypted_token))
            await temp_bot.delete_webhook()
            await temp_bot.session.close()
        except:
            pass

        # 2. Удаляем из БД (каскадно удалятся и документы, если настроено в моделях)
        await session.delete(agent)
        await session.commit()
        
        # Здесь также можно добавить вызов функции удаления векторов из Qdrant по agent_id
        
        await callback.answer("Агент полностью удален.", show_alert=True)
        await show_my_agents(callback, session) # Возвращаемся к списку
    else:
        await callback.answer("Агент уже был удален.")

@master_router.callback_query(F.data.startswith("delete_force_"))
async def delete_agent(callback: types.CallbackQuery, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])
    
    # 1. Получаем агента из БД
    agent = await session.get(Agent, agent_id)

    if agent:
        try:
            # 2. Удаляем вебхук в Telegram
            from core.crypto import decrypt_token
            temp_bot = Bot(token=decrypt_token(agent.encrypted_token))
            await temp_bot.delete_webhook()
            await temp_bot.session.close()
            
            # 3. Очищаем Qdrant (вызываем новую функцию)
            await delete_agent_vectors(agent_id)
            
            # 4. Удаляем из Postgres
            # Благодаря cascade="all, delete-orphan", документы удалятся сами!
            await session.delete(agent)
            await session.commit()
            
            await callback.answer("Агент и все его данные успешно удалены.", show_alert=True)
            # Возвращаемся к списку агентов (импортируйте функцию show_my_agents если нужно)
            from handlers.master import show_my_agents
            await show_my_agents(callback, session)
            
        except Exception as e:
            await session.rollback()
            print(f"Ошибка при удалении: {e}")
            await callback.answer("Произошла ошибка при удалении.", show_alert=True)
    else:
        await callback.answer("Агент не найден.")