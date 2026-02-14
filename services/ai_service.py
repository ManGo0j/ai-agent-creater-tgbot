import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

ai_client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

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
    """Генерация ответа на основе динамического системного промпта и контекста."""
    
    # Формируем блок контекста из найденных чанков
    if not context_list:
        context_text = "Информации в базе знаний не найдено."
    else:
        context_parts = [f"Источник: {c['source']}\nТекст: {c['text']}" for c in context_list]
        context_text = "\n\n---\n\n".join(context_parts)

    user_prompt = f"КОНТЕКСТ ИЗ БАЗЫ ЗНАНИЙ:\n{context_text}\n\nВОПРОС ПОЛЬЗОВАТЕЛЯ: {question}"

    try:
        response = await ai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt}, # Берется из БД агента
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка при генерации ответа: {str(e)}"