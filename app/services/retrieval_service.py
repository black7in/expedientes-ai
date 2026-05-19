from sqlalchemy.orm import Session
from sqlalchemy import text

from .embedding_service import EmbeddingService


def recuperar_fragmentos(
    query: str,
    tipo_documento: str,
    materia: str = "civil",
    top_k_docs: int = 4,
    top_k_jurisprudencia: int = 3,
    top_k_leyes: int = 5,
    db: Session = None,
) -> dict:
    """
    Búsqueda semántica en las tres colecciones del RAG:
      - doc_chunks        → estilo y fórmulas del estudio Rosales y Asociados
      - jurisprudencia_chunks → Autos Supremos del TSJ Bolivia
      - leyes_chunks      → artículos de códigos bolivianos

    Retorna los fragmentos más relevantes de cada fuente para construir el prompt.
    Si una colección está vacía (no indexada aún), devuelve lista vacía sin error.
    """
    query_emb = EmbeddingService.encode_query(query)
    emb_str = EmbeddingService.to_pgvector_str(query_emb)

    # ── 1. Documentos del estudio (mismo tipo de documento) ──────────────────
    docs_rows = db.execute(
        text("""
            SELECT chunk_texto,
                   tipo_doc,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similitud
            FROM doc_chunks
            WHERE tipo_doc = :tipo_doc
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """),
        {"emb": emb_str, "tipo_doc": tipo_documento, "k": top_k_docs},
    ).fetchall()

    # ── 2. Jurisprudencia TSJ Bolivia ─────────────────────────────────────────
    juris_rows = db.execute(
        text("""
            SELECT chunk_texto,
                   numero_auto,
                   fecha_resolucion,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similitud
            FROM jurisprudencia_chunks
            WHERE materia = :materia
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """),
        {"emb": emb_str, "materia": materia, "k": top_k_jurisprudencia},
    ).fetchall()

    # ── 3. Artículos de leyes bolivianas ─────────────────────────────────────
    leyes_rows = db.execute(
        text("""
            SELECT chunk_texto,
                   ley,
                   numero_articulo,
                   titulo_articulo,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similitud
            FROM leyes_chunks
            WHERE materia = :materia
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :k
        """),
        {"emb": emb_str, "materia": materia, "k": top_k_leyes},
    ).fetchall()

    return {
        "docs_estudio": [
            {"texto": r[0], "tipo": r[1], "similitud": float(r[2])}
            for r in docs_rows
        ],
        "jurisprudencia": [
            {
                "texto": r[0],
                "auto": r[1],
                "fecha": str(r[2]) if r[2] else None,
                "similitud": float(r[3]),
            }
            for r in juris_rows
        ],
        "leyes": [
            {
                "texto": r[0],
                "ley": r[1],
                "articulo": r[2],
                "titulo": r[3],
                "similitud": float(r[4]),
            }
            for r in leyes_rows
        ],
    }
