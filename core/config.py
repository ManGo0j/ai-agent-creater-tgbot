import os
from dotenv import load_dotenv
from qdrant_client import AsyncQdrantClient

load_dotenv()

class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL")
    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
    QDRANT_URL = os.getenv("QDRANT_URL")
    QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") 
    BASE_URL = os.getenv("BASE_URL")
    MASTER_BOT_TOKEN = os.getenv("MASTER_BOT_TOKEN")

settings = Settings()

q_client = AsyncQdrantClient(
    url=settings.QDRANT_URL, 
    api_key=settings.QDRANT_API_KEY
)