from aiogram import Router, types
from services.search_service import search_knowledge_base
from services.ai_service import get_answer

agent_router = Router()

@agent_router.message()
async def handle_agent_message(message: types.Message, agent_config: dict):
    """
    Универсальный обработчик. 
    agent_config прилетел сюда из Middleware.
    """
    query = message.text
    agent_id = agent_config["id"]
    system_prompt = agent_config["system_prompt"]

    # 1. Поиск по базе знаний (только по этому агенту!)
    context = await search_knowledge_base(query, agent_id=agent_id)
    
    # 2. Генерация ответа через LLM с динамическим промптом
    answer = await get_answer(query, context, system_prompt)
    
    await message.answer(answer)