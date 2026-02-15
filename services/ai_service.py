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