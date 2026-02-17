from aiogram.fsm.state import State, StatesGroup

class CreateAgentSG(StatesGroup):
    waiting_token = State()
    waiting_prompt = State()
    waiting_docs = State()
    editing_prompt = State()
    adding_extra_docs = State()
    editing_welcome = State()