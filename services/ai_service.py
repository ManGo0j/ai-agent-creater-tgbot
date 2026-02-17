import os
import re
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

ai_client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

def clean_text(text: str) -> str:
    """
    Удаляет символы форматирования Markdown (# и *) и лишние пробелы.
    """
    if not text:
        return ""
    # 1. Удаляем решетки (заголовки) и звездочки (жирный/курсив)
    text = re.sub(r'[#*]', '', text)
    # 2. Очищаем каждую строку от лишних пробелов по краям
    lines = [line.strip() for line in text.splitlines()]
    # 3. Собираем обратно, убирая пустые строки в начале и конце
    return "\n".join(lines).strip()

async def rewrite_query(original_query: str) -> str:
    """Оптимизация запроса пользователя для векторного поиска."""
    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Переформулируй запрос пользователя в поисковый запрос для базы знаний. Верни только текст запроса."},
                {"role": "user", "content": original_query}
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception:
        return original_query # Если упало — ищем по оригиналу

async def get_answer(question: str, context_list: list, system_prompt: str) -> str:
    """Генерация ответа на основе динамического системного промпта и контекста с очисткой от Markdown."""
    
    # Формируем блок контекста из найденных чанков
    if not context_list:
        context_text = "Информации в базе знаний не найдено."
    else:
        context_parts = [f"Источник: {c['source']}\nТекст: {c['text']}" for c in context_list]
        context_text = "\n\n---\n\n".join(context_parts)

    # Усиливаем системный промпт инструкцией о запрете Markdown
    full_system_prompt = f"""{system_prompt}

        ВАЖНО: Отвечай только чистым текстом. 
        ЗАПРЕЩЕНО использовать символы '*' для выделения жирным и символы '#' для заголовков. 
        Твой ответ должен быть легко читаемым без специального форматирования."""

    user_prompt = f"КОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n{context_text}\n\nВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}"

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": full_system_prompt}, 
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        
        raw_answer = response.choices[0].message.content
        
        # Применяем фильтрацию (удаление оставшихся * и #)
        return clean_text(raw_answer)
        
    except Exception as e:
        return f"Ошибка при генерации ответа: {str(e)}"
    
async def generate_welcome_with_ai(system_prompt: str) -> str:
    """Генерирует приветствие на основе системного промпта агента."""
    prompt = (
        "Ты профессиональный копирайтер. Напиши короткое, дружелюбное и вовлекающее приветственное "
        "сообщение (максимум 2-3 предложения) для Telegram-бота от первого лица. "
        "Пользователь увидит это сообщение после нажатия кнопки /start.\n\n"
        "Обязательно опирайся на системный промпт бота, чтобы передать его характер и суть работы.\n"
        "Пиши ТОЛЬКО текст приветствия, без кавычек и лишних пояснений.\n\n"
        f"Системный промпт бота:\n{system_prompt}"
        """ВАЖНО: Отвечай только чистым текстом. 
        ЗАПРЕЩЕНО использовать символы '*' для выделения жирным и символы '#' для заголовков. 
        Твой ответ должен быть легко читаемым без специального форматирования."""
    )
    
    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat", 
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Ошибка при генерации приветствия: {e}")
        return "Произошла ошибка при генерации приветствия. Пожалуйста, попробуйте задать его вручную."
    
async def improve_prompt_with_ai(current_prompt: str) -> str:
    """Превращает короткое описание в структурированный системный промпт."""
    instruction = (
        "Ты — эксперт по разработке системных промптов для больших языковых моделей. "
        "Твоя задача: взять сырое описание роли бота и превратить его в четкую, структурированную инструкцию.\n\n"
        "Используй следующую структуру:\n"
        "1. Роль и контекст.\n"
        "2. Основные задачи.\n"
        "3. Стиль общения и ограничения.\n"
        "4. Правило: всегда использовать предоставленные документы из базы знаний.\n\n"
        f"Текущее описание/промпт: {current_prompt}\n\n"
        "Напиши только текст итогового промпта, без лишних вступлений."
        """ВАЖНО: Отвечай только чистым текстом. 
        ЗАПРЕЩЕНО использовать символы '*' для выделения жирным и символы '#' для заголовков. 
        Твой ответ должен быть легко читаемым без специального форматирования."""
    )
    
    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat", 
            messages=[{"role": "user", "content": instruction}],
            temperature=1.0 # Чуть больше креативности для промпта
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ Ошибка улучшения промпта: {e}")
        return current_prompt # Возвращаем оригинал, если упал