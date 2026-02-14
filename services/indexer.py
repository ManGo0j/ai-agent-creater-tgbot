import os
import asyncio
import uuid  # Добавлен импорт для генерации валидных UUID
import pdfplumber
from docx import Document
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models
from fastembed import TextEmbedding, SparseTextEmbedding

from database.db import async_session
from database.models import AgentDocument
from core.config import settings

# Инициализация клиентов
qdrant_client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))
dense_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5") 
sparse_model = SparseTextEmbedding(model_name="prithivida/Splade_PP_en_v1")

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=100,
    separators=["\n\n", "\n", ".", " ", ""]
)

async def extract_text(file_path: str) -> str:
    """Извлекает текст в зависимости от расширения файла."""
    ext = os.path.splitext(file_path)[1].lower()
    text = ""
    if ext == ".pdf":
        with pdfplumber.open(file_path) as pdf:
            text = "".join([page.extract_text() or "" for page in pdf.pages])
    elif ext == ".docx":
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
    elif ext == ".txt":
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    return text

async def process_document(file_path: str, agent_id: int, document_id: int):
    """
    Фоновая задача для обработки документа:
    Парсинг -> Чанкинг -> Эмбеддинги -> Qdrant -> Обновление БД
    """
    try:
        # 1. Извлечение текста
        text = await extract_text(file_path)
        if not text:
            raise ValueError("Не удалось извлечь текст из файла")

        # 2. Нарезка на чанки
        chunks = text_splitter.split_text(text)
        
        # 3. Генерация эмбеддингов и загрузка в Qdrant
        points = []
        for i, chunk_text in enumerate(chunks):
            # Генерируем векторы
            dense_vector = list(dense_model.embed([chunk_text]))[0]
            sparse_vector = list(sparse_model.embed([chunk_text]))[0]

            # ИСПРАВЛЕНИЕ: Генерируем валидный UUID на основе document_id и индекса чанка
            # Это решает ошибку "value 1_0 is not a valid point ID"
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}_{i}"))

            points.append(
                models.PointStruct(
                    id=point_id,
                    # Исправленный синтаксис передачи векторов:
                    vector={
                        "": dense_vector.tolist(),  # Безымянный (основной) вектор
                        "sparse-text": models.SparseVector(
                            indices=sparse_vector.indices.tolist(),
                            values=sparse_vector.values.tolist()
                        )
                    },
                    payload={
                        "agent_id": agent_id,
                        "document_id": document_id,
                        "text": chunk_text,
                        "source": os.path.basename(file_path)
                    }
                )
            )

        # Загружаем в Qdrant
        qdrant_client.upsert(
            collection_name="agent_documents",
            points=points
        )

        # 4. Обновляем статус в БД на 'ready'
        async with async_session() as session:
            await session.execute(
                update(AgentDocument)
                .where(AgentDocument.id == document_id)
                .values(status="ready")
            )
            await session.commit()

    except Exception as e:
        print(f"Ошибка при индексации документа {document_id}: {e}")
        async with async_session() as session:
            await session.execute(
                update(AgentDocument)
                .where(AgentDocument.id == document_id)
                .values(status="error")
            )
            await session.commit()
    finally:
        # Удаляем временный файл после обработки
        if os.path.exists(file_path):
            os.remove(file_path)