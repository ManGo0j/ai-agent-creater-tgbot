from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

def get_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Профиль", callback_data="profile")
    builder.button(text="🤖 Мои агенты", callback_data="my_agents")
    builder.button(text="➕ Создать агента", callback_data="add_agent")
    builder.adjust(2)
    return builder.as_markup()