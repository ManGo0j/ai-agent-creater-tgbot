from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

def get_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Профиль", callback_data="profile")
    builder.button(text="🤖 Мои агенты", callback_data="my_agents")
    builder.button(text="➕ Создать агента", callback_data="add_agent")
    builder.button(text="💎 Тарифы", callback_data="tariffs_menu") # Добавили кнопку
    builder.adjust(2, 1, 1) # Красивая сетка: 2 кнопки, потом 1, потом 1
    return builder.as_markup()

def get_tariffs_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Выбрать 'Продвинутый'", callback_data="set_plan_Advanced")
    builder.button(text="Выбрать 'Pro'", callback_data="set_plan_Pro")
    builder.button(text="⬅️ Назад", callback_data="back_to_start")
    builder.adjust(1)
    return builder.as_markup()