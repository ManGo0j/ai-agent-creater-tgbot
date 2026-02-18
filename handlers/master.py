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
from services.search_service import delete_document_vectors
from services.ai_service import generate_welcome_with_ai
from services.ai_service import improve_prompt_with_ai

from datetime import datetime, timedelta
from sqlalchemy import select, update, func
from database.models import User
from keyboards.master_kb import get_main_menu, get_tariffs_keyboard

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
    # Логика регистрации пользователя
    res = await session.execute(select(User).where(User.telegram_id == message.from_user.id))
    user = res.scalar_one_or_none()
    
    if not user:
        user = User(
            telegram_id=message.from_user.id, 
            username=message.from_user.username,
            subscription_type="Free" # Явно задаем базовый тариф при регистрации
        )
        session.add(user)
        await session.commit()
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! Это конструктор AI-агентов.\n\n"
        "Здесь ты можешь создать своего бота с кастомными промптами и базой знаний.",
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
async def start_add_agent(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    # 1. Получаем данные пользователя и его текущий тариф
    res = await session.execute(select(User).where(User.telegram_id == callback.from_user.id))
    user = res.scalar_one_or_none()
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден в базе.", show_alert=True)
        return

    # 2. Считаем, сколько агентов уже создал этот пользователь
    count_res = await session.execute(
        select(func.count(Agent.id)).where(Agent.owner_id == user.id)
    )
    agents_count = count_res.scalar() or 0

    # 3. Определяем лимиты согласно ТЗ
    # Базовый (Free) — 1, Продвинутый — 5, Pro — 20
    limits = {
        "Free": 1,
        "Advanced": 5,
        "Pro": 20
    }
    
    current_limit = limits.get(user.subscription_type, 1)

    # 4. Проверяем превышение лимита
    if agents_count >= current_limit:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💎 Повысить тариф", callback_data="tariffs_menu")],
            [types.InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_start")]
        ])
        
        await callback.message.edit_text(
            f"🚫 *Лимит достигнут*\n\n"
            f"На вашем тарифе (*{user.subscription_type}*) можно создать не более {current_limit} агентов.\n"
            f"У вас уже создано: {agents_count}.\n\n"
            f"Чтобы создавать больше ботов, пожалуйста, обновите тарифную подписку.",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        await callback.answer()
        return

    # 5. Если лимит не превышен, запускаем стандартный процесс создания
    await state.set_state(CreateAgentSG.waiting_token)
    await callback.message.answer(
        "🤖 *Создание нового агента*\n\n"
        "Для начала работы мне нужен API токен вашего бота.\n"
        "Получить его можно у @BotFather.",
        parse_mode="Markdown"
    )
    await callback.answer()

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
    
    # 1. Сначала скачиваем файл во временную папку для анализа
    os.makedirs("temp_uploads", exist_ok=True)
    file_path = f"temp_uploads/{file_id}_{file_name}"
    await bot.download(message.document, destination=file_path)

    # 2. Предварительная проверка лимитов (Этап 4)
    from services.indexer import extract_text, text_splitter, get_current_chunks_count, CHUNK_LIMITS
    
    # Получаем тариф пользователя
    result = await session.execute(select(User).join(Agent).where(Agent.id == agent_id))
    user = result.scalar_one_or_none()
    limit = CHUNK_LIMITS.get(user.subscription_type, 100)

    # Извлекаем текст и считаем чанки
    text = await extract_text(file_path)
    chunks = text_splitter.split_text(text)
    new_chunks_count = len(chunks)
    
    current_count = await get_current_chunks_count(agent_id)

    if current_count + new_chunks_count > limit:
        if os.path.exists(file_path):
            os.remove(file_path)
        
        await message.answer(
            f"🚫 *Лимит превышен!*\n\n"
            f"Ваш тариф: *{user.subscription_type}* (лимит {limit} чанков).\n"
            f"Уже использовано: {current_count}.\n"
            f"Этот файл добавит еще {new_chunks_count} чанков.\n\n"
            f"Пожалуйста, удалите старые файлы или повысьте тариф в меню.",
            reply_markup=get_tariffs_keyboard(),
            parse_mode="Markdown"
        )
        return

    # 3. Если лимит не превышен — создаем запись в БД и запускаем обработку
    new_doc = AgentDocument(
        agent_id=agent_id, 
        file_name=file_name, 
        file_id=file_id, 
        status="processing"
    )
    session.add(new_doc)
    await session.commit()
    
    # Запускаем фоновую индексацию (теперь она точно пройдет по лимитам)
    asyncio.create_task(process_document(file_path, agent_id, new_doc.id))
    
    await message.answer(
        f"✅ Файл '_{escape_md(file_name)}_' принят и обрабатывается ({new_chunks_count} чанков).",
        parse_mode="Markdown"
    )

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
        
        status_emoji = "🟢" if agent.is_active else "🔴"
        bot_name = f"@{agent.bot_username}" if agent.bot_username else f"Агент #{agent.id}"
        button_text = f"{status_emoji} {bot_name}"
        
        builder.button(text=button_text, callback_data=f"agent_info_{agent.id}")
    
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
    welcome_display = agent.welcome_message if agent.welcome_message else "❌ Не установлено"
    docs_res = await session.execute(
        select(func.count(AgentDocument.id)).where(AgentDocument.agent_id == agent_id)
    )
    docs_count = docs_res.scalar()

    bot_name = escape_md(agent.bot_username) if agent.bot_username else "Бот"
    status_text = "✅ Активен" if agent.is_active else "❌ Отключен"
    toggle_label = "🔴 Отключить" if agent.is_active else "🟢 Включить"
    
    text = (
        f"🤖 *Управление агентом*\n\n"
        f"ID: `{agent.id}`\n"
        f"🔗 *Бот:* @{bot_name}\n"
        f"📊 *Статус:* {status_text}\n"
        f"📚 *Документов:* {docs_count}\n"
        f"👋 *Приветствие:* {welcome_display}\n\n"
        f"🧠 *Промпт:* \n_{escape_md(agent.system_prompt[:200])}..._"
    )

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
       [
        types.InlineKeyboardButton(text="📝 Изменить промпт", callback_data=f"edit_prompt_{agent_id}"),
        types.InlineKeyboardButton(text="👋 Изменить приветствие", callback_data=f"edit_welcome_{agent_id}")
        ],
        [types.InlineKeyboardButton(text="📚 Редактировать базу знаний", callback_data=f"edit_kb_{agent_id}")],
        [
            types.InlineKeyboardButton(text=toggle_label, callback_data=f"toggle_agent_{agent_id}"),
            types.InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"confirm_delete_{agent_id}")
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

# --- РЕДАКТИРОВАНИЕ ПРОМПТА ---

@master_router.callback_query(F.data.startswith("edit_prompt_"))
async def start_edit_prompt(callback: types.CallbackQuery, state: FSMContext):
    agent_id = int(callback.data.split("_")[2])
    await state.update_data(edit_agent_id=agent_id)
    await state.set_state(CreateAgentSG.editing_prompt)
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✨ Улучшить текущий через ИИ", callback_data=f"ai_improve_prompt_{agent_id}")]
    ])
    
    await callback.message.answer(
        "📝 *Редактирование системного промпта*\n\n"
        "Введите новую инструкцию для бота. Опишите, как он должен себя вести и на какие вопросы отвечать.\n\n"
        "💡 *Совет:* Чем подробнее инструкция, тем лучше результат.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@master_router.callback_query(F.data.startswith("ai_improve_prompt_"))
async def process_ai_improve_prompt(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    agent_id = int(callback.data.split("_")[3])
    
    # 1. Получаем текущий промпт агента
    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    
    if not agent or not agent.system_prompt:
        await callback.answer("❌ Сначала введите хотя бы краткое описание роли!", show_alert=True)
        return

    # Визуальный фидбек пользователю
    await callback.message.edit_text(
        "*LLM модель обрабатывает промпт...*", 
        parse_mode="Markdown"
    )
    
    # 2. Генерируем улучшение через сервис
    # Убедись, что improve_prompt_with_ai импортирована из services.ai_service
    new_prompt = await improve_prompt_with_ai(agent.system_prompt)
    
    # 3. Сохраняем новый промпт в базу данных
    await session.execute(
        update(Agent).where(Agent.id == agent_id).values(system_prompt=new_prompt)
    )
    await session.commit()
    
    # Сбрасываем состояние FSM, так как редактирование завершено
    await state.clear()
    
    # Экранируем текст для безопасного отображения в Markdown
    # Это предотвратит ошибку "can't parse entities", если ИИ выдаст много спецсимволов
    safe_new_prompt = escape_md(new_prompt)
    
    # 4. Отправляем красивое сообщение с результатом (как при приветствии)
    await callback.message.answer(
        f"✅ ИИ модель придумала отличный промпт :\n\n_{safe_new_prompt}_",
        parse_mode="Markdown"
    )
    
    # 5. Возвращаем пользователя к карточке управления агентом
    from handlers.master import show_agent_info
    fake_callback = types.CallbackQuery(
        id="0", 
        from_user=callback.from_user, 
        chat_instance="0",
        message=callback.message, 
        data=f"agent_info_{agent_id}"
    )
    await show_agent_info(fake_callback, session)

@master_router.message(CreateAgentSG.editing_prompt)
async def process_new_prompt(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    agent_id = data.get('edit_agent_id')
    
    if not agent_id:
        await message.answer("Ошибка: ID агента потерян. Попробуй заново через меню.")
        await state.clear()
        return

    # Обновляем промпт в базе
    await session.execute(
        update(Agent).where(Agent.id == agent_id).values(system_prompt=message.text)
    )
    await session.commit()
    
    await state.clear()
    
    # Сразу показываем обновленную карточку агента
    # Для этого имитируем callback
    fake_callback = types.CallbackQuery(
        id="0",
        from_user=message.from_user,
        chat_instance="0",
        message=message,
        data=f"agent_info_{agent_id}"
    )
    
    await message.answer("✅ Системный промпт успешно обновлен!")
    await show_agent_info(fake_callback, session)

# --- УПРАВЛЕНИЕ БАЗОЙ ЗНАНИЙ (ДОКУМЕНТЫ) ---

@master_router.callback_query(F.data.startswith("edit_kb_"))
async def show_knowledge_base(callback: types.CallbackQuery, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])

    # Получаем все документы агента
    docs_res = await session.execute(
        select(AgentDocument).where(AgentDocument.agent_id == agent_id).order_by(AgentDocument.created_at.desc())
    )
    docs = docs_res.scalars().all()

    builder = InlineKeyboardBuilder()

    if docs:
        for doc in docs:
            # Обрезаем имя файла, если оно слишком длинное (Telegram лимит на кнопки)
            short_name = doc.file_name[:25] + "..." if len(doc.file_name) > 25 else doc.file_name
            # Индикаторы статуса
            status_emoji = "⏳" if doc.status == "processing" else "✅" if doc.status == "ready" else "❌"
            
            builder.button(
                text=f"🗑 {status_emoji} {short_name}",
                callback_data=f"del_doc_conf_{doc.id}"
            )
        builder.adjust(1) # По одной кнопке в ряд
    
    # Кнопки навигации
    # builder.row(types.InlineKeyboardButton(text="➕ Добавить файл", callback_data=f"add_doc_{agent_id}")) # Задел на будущее
    builder.row(types.InlineKeyboardButton(text="➕ Добавить файл", callback_data=f"add_doc_{agent_id}"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад к агенту", callback_data=f"agent_info_{agent_id}"))

    text = (
        "📚 *Управление базой знаний*\n\n"
        "Нажмите на файл, который хотите удалить.\n\n"
        "Легенда:\n"
        "✅ — Успешно загружен в ИИ\n"
        "⏳ — В процессе обработки\n"
        "❌ — Ошибка чтения файла"
    ) if docs else "📚 *Управление базой знаний*\n\nВ базе данных этого агента пока нет файлов."

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


# --- ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ ДОКУМЕНТА ---

@master_router.callback_query(F.data.startswith("del_doc_conf_"))
async def confirm_delete_document(callback: types.CallbackQuery, session: AsyncSession):
    # callback_data имеет вид "del_doc_conf_15", id под индексом 3
    doc_id = int(callback.data.split("_")[3])

    doc = await session.get(AgentDocument, doc_id)
    if not doc:
        return await callback.answer("Ошибка: документ не найден.", show_alert=True)

    text = f"⚠️ *ВНИМАНИЕ!*\n\nВы действительно хотите навсегда удалить файл `{escape_md(doc.file_name)}`?\nБот больше не сможет использовать его для ответов."

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ ОТМЕНА", callback_data=f"edit_kb_{doc.agent_id}"),
            types.InlineKeyboardButton(text="❌ ДА, УДАЛИТЬ", callback_data=f"del_doc_force_{doc.id}")
        ]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")


# --- ФАКТИЧЕСКОЕ УДАЛЕНИЕ ДОКУМЕНТА ---

@master_router.callback_query(F.data.startswith("del_doc_force_"))
async def force_delete_document(callback: types.CallbackQuery, session: AsyncSession):
    doc_id = int(callback.data.split("_")[3])

    doc = await session.get(AgentDocument, doc_id)
    if not doc:
        return await callback.answer("Документ уже был удален.")

    agent_id = doc.agent_id

    try:
        # 1. Удаляем векторы из векторной БД Qdrant
        await delete_document_vectors(doc_id)

        # 2. Удаляем запись из Postgres
        await session.delete(doc)
        await session.commit()

        await callback.answer("✅ Файл успешно удален из базы знаний!", show_alert=True)
    except Exception as e:
        await session.rollback()
        print(f"Ошибка при удалении документа: {e}")
        await callback.answer("Произошла ошибка при удалении.", show_alert=True)

    # Возвращаемся обратно в меню базы знаний (генерируем фейковый callback)
    fake_callback = types.CallbackQuery(
        id="0", from_user=callback.from_user, chat_instance="0",
        message=callback.message, data=f"edit_kb_{agent_id}"
    )
    await show_knowledge_base(fake_callback, session)

# --- ДОБАВЛЕНИЕ НОВОГО ДОКУМЕНТА (ЗАПРОС) ---

@master_router.callback_query(F.data.startswith("add_doc_"))
async def prompt_add_document(callback: types.CallbackQuery, state: FSMContext):
    agent_id = int(callback.data.split("_")[2])
    
    # Запоминаем, какому агенту добавляем файл
    await state.update_data(edit_agent_id=agent_id)
    await state.set_state(CreateAgentSG.adding_extra_docs)
    
    # Кнопка отмены, чтобы вернуться в список документов
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"edit_kb_{agent_id}")]
    ])
    
    await callback.message.edit_text(
        "📂 *Добавление нового файла*\n\n"
        "Отправьте мне документ (PDF, TXT, DOCX), который нужно загрузить в базу знаний.\n"
        "Можно отправлять по одному файлу.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# --- ПРИЕМ И ОБРАБОТКА НОВОГО ДОКУМЕНТА ---

@master_router.message(CreateAgentSG.adding_extra_docs, F.document)
async def process_extra_document(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot):
    data = await state.get_data()
    agent_id = data.get('edit_agent_id')
    
    if not agent_id:
        await message.answer("❌ Ошибка: потерян ID агента. Начните сначала.")
        await state.clear()
        return

    file_name = message.document.file_name
    file_id = message.document.file_id

    # Временное сообщение
    msg = await message.answer(f"⏳ Проверяю лимиты и анализирую файл `{file_name}`...")

    try:
        # 1. Скачиваем файл для предварительного анализа чанков
        os.makedirs("temp_uploads", exist_ok=True)
        file_path = f"temp_uploads/{file_id}_{file_name}"
        await bot.download(message.document, destination=file_path)

        # 2. Импортируем инструменты лимитов из индексера
        from services.indexer import extract_text, text_splitter, get_current_chunks_count, CHUNK_LIMITS, process_document
        
        # 3. Получаем тариф пользователя (через владельца агента)
        from database.models import User, Agent
        result = await session.execute(
            select(User).join(Agent).where(Agent.id == agent_id)
        )
        user = result.scalar_one_or_none()
        
        current_plan = user.subscription_type if user else "Free"
        limit = CHUNK_LIMITS.get(current_plan, 100)

        # 4. Считаем чанки в новом файле
        text = await extract_text(file_path)
        chunks = text_splitter.split_text(text)
        new_chunks_count = len(chunks)
        
        # Считаем текущее кол-во чанков в Qdrant
        current_count = await get_current_chunks_count(agent_id)

        # 5. ПРОВЕРКА: Проходит ли файл в лимит?
        if current_count + new_chunks_count > limit:
            if os.path.exists(file_path):
                os.remove(file_path)
            
            await msg.edit_text(
                f"🚫 *Лимит базы знаний превышен!*\n\n"
                f"Ваш тариф: *{current_plan}* (макс. {limit} чанков).\n"
                f"Уже использовано: {current_count}.\n"
                f"Файл содержит: {new_chunks_count}.\n\n"
                f"Удалите старые документы или повысьте тариф в меню.",
                parse_mode="Markdown"
            )
            return

        # 6. Если всё хорошо — фиксируем в Postgres и запускаем фон
        new_doc = AgentDocument(
            agent_id=agent_id, 
            file_name=file_name, 
            file_id=file_id, 
            status="processing"
        )
        session.add(new_doc)
        await session.commit()

        asyncio.create_task(process_document(file_path, agent_id, new_doc.id))
        await msg.edit_text(f"✅ Файл `{file_name}` принят и обрабатывается ({new_chunks_count} чанков).")

    except Exception as e:
        print(f"❌ Ошибка в process_extra_document: {e}")
        await msg.edit_text(f"❌ Ошибка при обработке файла: {e}")
        if 'file_path' in locals() and os.path.exists(file_path):
            os.remove(file_path)

    # 7. Возврат в меню базы знаний (через небольшую паузу, чтобы успели прочитать)
    await asyncio.sleep(2)
    from handlers.master import show_knowledge_base
    fake_callback = types.CallbackQuery(
        id="0", from_user=message.from_user, chat_instance="0",
        message=message, data=f"edit_kb_{agent_id}"
    )
    await show_knowledge_base(fake_callback, session)
@master_router.callback_query(F.data.startswith("edit_welcome_"))
async def start_edit_welcome(callback: types.CallbackQuery, state: FSMContext):
    agent_id = int(callback.data.split("_")[2])
    await state.update_data(edit_agent_id=agent_id)
    await state.set_state(CreateAgentSG.editing_welcome)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✨ Сгенерировать с ИИ", callback_data=f"gen_welcome_{agent_id}")]
    ])
    await callback.message.answer("Введите новое приветственное сообщение или сгенерируйте его с помощью ИИ, которое пользователь увидит при команде /start:", reply_markup=kb)
    await callback.answer()

@master_router.message(CreateAgentSG.editing_welcome)
async def process_welcome_message(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    agent_id = data.get('edit_agent_id')
    
    await session.execute(
        update(Agent).where(Agent.id == agent_id).values(welcome_message=message.text)
    )
    await session.commit()
    await state.clear()
    await message.answer("✅ Приветствие сохранено!")

@master_router.callback_query(F.data.startswith("gen_welcome_"))
async def generate_welcome_callback(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    agent_id = int(callback.data.split("_")[2])
    
    # Меняем сообщение, чтобы пользователь видел процесс
    await callback.message.edit_text("⏳ *DeepSeek анализирует промпт и генерирует приветствие...*", parse_mode="Markdown")
    
    # 1. Достаем агента из БД, чтобы получить его system_prompt
    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    
    if not agent:
        await callback.answer("Агент не найден", show_alert=True)
        return
        
    # 2. Генерируем текст через ИИ
    generated_text = await generate_welcome_with_ai(agent.system_prompt)
    
    # 3. Сохраняем в БД
    await session.execute(
        update(Agent).where(Agent.id == agent_id).values(welcome_message=generated_text)
    )
    await session.commit()
    
    # 4. Очищаем состояние (пользователю больше не нужно вводить текст вручную)
    await state.clear()
    
    # 5. Отправляем результат
    await callback.message.answer(
        f"✅ *ИИ модель придумала отличное приветствие:*\n\n_{generated_text}_", 
        parse_mode="Markdown"
    )
    
    # 6. Возвращаем пользователя в меню карточки агента
    from handlers.master import show_agent_info
    fake_callback = types.CallbackQuery(
        id="0", from_user=callback.from_user, chat_instance="0",
        message=callback.message, data=f"agent_info_{agent_id}"
    )
    await show_agent_info(fake_callback, session)

@master_router.callback_query(F.data == "tariffs_menu")
async def show_tariffs(callback: types.CallbackQuery, session: AsyncSession):
    """Отображение меню тарифов и текущего статуса пользователя."""
    # Получаем данные пользователя из БД
    result = await session.execute(
        select(User).where(User.telegram_id == callback.from_user.id)
    )
    user = result.scalar_one_or_none()
    
    # Если пользователя вдруг нет, или у него нет тарифа, ставим Free
    current_plan = user.subscription_type if user and user.subscription_type else "Free"

    text = (
        f"💎 *Управление подпиской*\n\n"
        f"Ваш текущий тариф: *{current_plan}*\n\n"
        f"🚀 *Доступные планы:*\n\n"
        f"1️⃣ *Базовый (Free)*\n"
        f"— 1 активный агент\n"
        f"— Лимит базы знаний: 100 чанков\n"
        f"— Цена: 0₽/мес\n\n"
        f"2️⃣ *Продвинутый*\n"
        f"— До 5 активных агентов\n"
        f"— Лимит базы знаний: 500 чанков\n"
        f"— Цена: 1 990₽/мес\n\n"
        f"3️⃣ *Pro*\n"
        f"— До 20 активных агентов\n"
        f"— Лимит базы знаний: Безлимит\n"
        f"— Цена: 9 990₽/мес\n"
    )

    await callback.message.edit_text(
        text, 
        reply_markup=get_tariffs_keyboard(), 
        parse_mode="Markdown"
    )


@master_router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: types.CallbackQuery):
    """Возврат в главное меню из тарифов."""
    await callback.message.edit_text(
        "👋 Привет! Я Мастер-бот для создания AI-агентов.\n\n"
        "Здесь ты можешь создать своего бота с кастомными промптами и базой знаний.",
        reply_markup=get_main_menu()
    )


@master_router.callback_query(F.data.startswith("set_plan_"))
async def process_set_plan(callback: types.CallbackQuery, session: AsyncSession):
    """Имитация оплаты: переключение тарифа в БД."""
    plan_name = callback.data.split("_")[2] # Достаем название плана (Advanced или Pro)
    
    # Имитируем оплату: ставим тариф на 30 дней вперед
    end_date = datetime.utcnow() + timedelta(days=30)
    
    # Обновляем запись пользователя в базе
    await session.execute(
        update(User)
        .where(User.telegram_id == callback.from_user.id)
        .values(
            subscription_type=plan_name,
            subscription_end_date=end_date
        )
    )
    await session.commit()
    
    await callback.answer(f"✅ Тариф {plan_name} успешно активирован на 30 дней!", show_alert=True)
    
    # Сразу обновляем интерфейс, чтобы пользователь увидел изменения
    await show_tariffs(callback, session)