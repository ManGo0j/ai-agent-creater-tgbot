from datetime import datetime
from sqlalchemy import BigInteger, ForeignKey, String, Text, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

class User(Base):
    """Владелец платформы (тот, кто создает ботов)."""
    __tablename__ = "users"

    subscription_type: Mapped[str] = mapped_column(String(50), default="Free")
    subscription_end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    agents: Mapped[list["Agent"]] = relationship(back_populates="owner")

class Agent(Base):
    """Настройки конкретного AI-агента."""
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    
    # Храним зашифрованный токен
    encrypted_token: Mapped[str] = mapped_column(String(500), unique=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=True) 
    bot_username: Mapped[str] = mapped_column(String, nullable=True)
    
    system_prompt: Mapped[str] = mapped_column(Text, default="Ты — полезный ассистент.")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_message = mapped_column(Text, nullable=True)
    
    owner: Mapped["User"] = relationship(back_populates="agents")
    
    documents: Mapped[list["AgentDocument"]] = relationship(
        back_populates="agent", 
        cascade="all, delete-orphan"
    )

class AgentDocument(Base):
    """Метаданные файлов, на которых обучен конкретный агент."""
    __tablename__ = "agent_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    
    file_name: Mapped[str] = mapped_column(String(255))
    file_id: Mapped[str] = mapped_column(String(255))  # Telegram File ID
    status: Mapped[str] = mapped_column(String(50), default="processing") # processing, ready, error
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    agent: Mapped["Agent"] = relationship(back_populates="documents")

class EndUser(Base):
    """Клиенты, которые пишут ботам-агентам."""
    __tablename__ = "end_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"))
    
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)