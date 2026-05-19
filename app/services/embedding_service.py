import threading
import os
from sentence_transformers import SentenceTransformer


class EmbeddingService:
    """
    Wrapper singleton para el modelo intfloat/multilingual-e5-large.
    - 1024 dimensiones
    - Multilingüe (incluye español jurídico)
    - Corre en CPU sin GPU
    - Se descarga automáticamente la primera vez (~1.2GB, luego queda en caché)
    """

    _instance: SentenceTransformer | None = None
    _lock = threading.Lock()
    MODEL_NAME = "intfloat/multilingual-e5-large"

    @classmethod
    def get_instance(cls) -> SentenceTransformer:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cache_dir = os.getenv("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
                    cls._instance = SentenceTransformer(
                        cls.MODEL_NAME,
                        cache_folder=cache_dir,
                    )
        return cls._instance

    @classmethod
    def encode_passages(cls, texts: list[str]) -> list[list[float]]:
        """
        Codifica una lista de textos como 'passages' (fragmentos de documentos).
        El prefijo 'passage:' es requerido por el modelo e5 para búsqueda asimétrica.
        """
        model = cls.get_instance()
        prefixed = [f"passage: {t}" for t in texts]
        embeddings = model.encode(
            prefixed,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=16,
        )
        return embeddings.tolist()

    @classmethod
    def encode_query(cls, query: str) -> list[float]:
        """
        Codifica una consulta de búsqueda.
        El prefijo 'query:' es requerido por el modelo e5.
        """
        model = cls.get_instance()
        embedding = model.encode(
            f"query: {query}",
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embedding.tolist()

    @staticmethod
    def to_pgvector_str(embedding: list[float]) -> str:
        """Convierte embedding a string compatible con pgvector: '[0.1,0.2,...]'"""
        return "[" + ",".join(map(str, embedding)) + "]"
