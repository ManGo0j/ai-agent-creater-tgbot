import os
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models
from fastembed import TextEmbedding, SparseTextEmbedding
from services.ai_service import rewrite_query

# Инициализация моделей (как в старом коде)
qdrant_client = QdrantClient(url=os.getenv("QDRANT_URL"), api_key=os.getenv("QDRANT_API_KEY"))
dense_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
sparse_model = SparseTextEmbedding(model_name="prithivida/Splade_PP_en_v1")

async def search_knowledge_base(query: str, agent_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Гибридный поиск с фильтрацией по agent_id.
    """
    # 1. Пересборка запроса для лучшего поиска (Query Rewriting)
    optimized_query = await rewrite_query(query)
    
    # 2. Генерация векторов
    dense_vector = list(dense_model.embed([optimized_query]))[0].tolist()
    sparse_vector = list(sparse_model.embed([optimized_query]))[0]

    # 3. Настройка фильтра (Изоляция данных)
    search_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="agent_id", 
                match=models.MatchValue(value=agent_id)
            )
        ]
    )

    # 4. Гибридный поиск (RRF)
    search_result = qdrant_client.search_batch(
        collection_name="agent_documents",
        requests=[
            models.SearchRequest(
                vector=models.NamedVector(name="default", vector=dense_vector),
                limit=limit,
                filter=search_filter,
                with_payload=True
            ),
            models.SearchRequest(
                vector=models.NamedSparseVector(
                    name="sparse-text", 
                    vector=models.SparseVector(
                        indices=sparse_vector.indices.tolist(),
                        values=sparse_vector.values.tolist()
                    )
                ),
                limit=limit,
                filter=search_filter,
                with_payload=True
            )
        ]
    )

    # Плоское объединение результатов (простая реализация RRF или обычный extend)
    # В Qdrant можно использовать Prefetch для более точного RRF, но для SaaS 
    # фильтрация внутри SearchRequest — самый надежный способ изоляции.
    
    seen_ids = set()
    combined_results = []
    
    for response in search_result:
        for hit in response:
            if hit.id not in seen_ids:
                combined_results.append({
                    "text": hit.payload.get("text"),
                    "source": hit.payload.get("source"),
                    "score": hit.score
                })
                seen_ids.add(hit.id)

    return combined_results[:limit]