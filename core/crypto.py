import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

# В реальном проекте сгенерируйте ключ через Fernet.generate_key() 
# и надежно сохраните его в .env
SECRET_KEY = os.getenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
cipher_suite = Fernet(SECRET_KEY.encode())

def encrypt_token(token: str) -> str:
    """Шифрует токен бота для хранения в БД."""
    return cipher_suite.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    """Дешифрует токен бота для использования в API."""
    return cipher_suite.decrypt(encrypted_token.encode()).decode()