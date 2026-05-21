import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from .embedding_service import EmbeddingService

_MIN_CHUNKS = 1  # mínimo aceptable para proceder sin fallback


async def buscar_chunks(
    query: str,
    fuente: str,
    filtros: dict,
    top_k: int = 5,
    threshold: float = 0.4,
    db: AsyncSession = None,
) -> list[dict]:
    """
    Búsqueda semántica en una colección con filtros opcionales.

    fuente: 'doc_chunks' | 'leyes_chunks'
    filtros: {'tipo_doc': 'demanda', 'seccion': 'hechos', 'subtipo': 'ejecutiva'} para doc_chunks
             {'materia': 'civil'} para leyes_chunks
    """
    emb = await asyncio.to_thread(EmbeddingService.encode_query, query)
    emb_str = EmbeddingService.to_pgvector_str(emb)

    if fuente == "doc_chunks":
        return await _buscar_doc_chunks(emb_str, filtros, top_k, threshold, db)
    elif fuente == "leyes_chunks":
        return await _buscar_leyes_chunks(emb_str, filtros, top_k, threshold, db)
    else:
        raise ValueError(f"Fuente desconocida: {fuente}")


async def buscar_con_fallback(
    query: str,
    filtros_ideales: dict,
    top_k: int = 3,
    threshold: float = 0.4,
    db: AsyncSession = None,
) -> tuple[list[dict], str]:
    """
    Fallback en cascada para doc_chunks:
      1. subtipo exacto  → modo 'ideal'
      2. solo tipo_doc   → modo 'tipo_documento'
      3. sin filtros     → modo 'sin_filtros'

    Retorna (chunks, modo_usado).
    """
    # Intento 1: filtros completos (subtipo incluido)
    chunks = await buscar_chunks(query, "doc_chunks", filtros_ideales, top_k, threshold, db)
    if len(chunks) >= _MIN_CHUNKS:
        return chunks, "ideal"

    # Intento 2: solo tipo_doc, sin subtipo
    filtros_tipo = {k: v for k, v in filtros_ideales.items() if k != "subtipo"}
    if filtros_tipo != filtros_ideales:
        chunks = await buscar_chunks(query, "doc_chunks", filtros_tipo, top_k, threshold, db)
        if len(chunks) >= _MIN_CHUNKS:
            return chunks, "tipo_documento"

    # Intento 3: sin filtros de tipo (busca en todos los doc_chunks)
    filtros_seccion = {k: v for k, v in filtros_ideales.items() if k == "seccion"}
    chunks = await buscar_chunks(query, "doc_chunks", filtros_seccion, top_k, threshold, db)
    return chunks, "sin_filtros"


async def _buscar_doc_chunks(
    emb_str: str,
    filtros: dict,
    top_k: int,
    threshold: float,
    db: AsyncSession,
) -> list[dict]:
    condiciones = ["embedding IS NOT NULL", "1 - (embedding <=> CAST(:emb AS vector)) >= :threshold"]
    params: dict = {"emb": emb_str, "threshold": threshold, "top_k": top_k}

    if "tipo_doc" in filtros:
        condiciones.append("tipo_doc = :tipo_doc")
        params["tipo_doc"] = filtros["tipo_doc"]
    if "seccion" in filtros:
        condiciones.append("seccion = :seccion")
        params["seccion"] = filtros["seccion"]
    if "subtipo" in filtros:
        condiciones.append("subtipo = :subtipo")
        params["subtipo"] = filtros["subtipo"]

    where = " AND ".join(condiciones)
    result = await db.execute(
        text(f"""
            SELECT id, documento_id, tipo_doc, subtipo, seccion,
                   chunk_texto,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similitud
            FROM doc_chunks
            WHERE {where}
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :top_k
        """),
        params,
    )
    rows = result.fetchall()

    return [
        {
            "fuente":       "doc_chunks",
            "id":           r[0],
            "documento_id": str(r[1]) if r[1] else None,
            "tipo_doc":     r[2],
            "subtipo":      r[3],
            "seccion":      r[4],
            "extracto":     r[5][:300],
            "chunk_texto":  r[5],
            "similitud":    round(float(r[6]), 4),
        }
        for r in rows
    ]


async def _buscar_leyes_chunks(
    emb_str: str,
    filtros: dict,
    top_k: int,
    threshold: float,
    db: AsyncSession,
) -> list[dict]:
    condiciones = ["embedding IS NOT NULL", "1 - (embedding <=> CAST(:emb AS vector)) >= :threshold"]
    params: dict = {"emb": emb_str, "threshold": threshold, "top_k": top_k}

    if "materia" in filtros:
        val = filtros["materia"]
        if isinstance(val, list):
            for i, v in enumerate(val):
                params[f"materia_{i}"] = v
            placeholders = ", ".join(f":materia_{i}" for i in range(len(val)))
            condiciones.append(f"materia IN ({placeholders})")
        else:
            condiciones.append("materia = :materia")
            params["materia"] = val
    if "ley" in filtros:
        condiciones.append("ley = :ley")
        params["ley"] = filtros["ley"]
    if "numero_articulo" in filtros:
        val = filtros["numero_articulo"]
        if isinstance(val, list):
            for i, v in enumerate(val):
                params[f"art_{i}"] = str(v)
            placeholders = ", ".join(f":art_{i}" for i in range(len(val)))
            condiciones.append(f"numero_articulo IN ({placeholders})")
        else:
            condiciones.append("numero_articulo = :numero_articulo")
            params["numero_articulo"] = str(val)

    where = " AND ".join(condiciones)
    result = await db.execute(
        text(f"""
            SELECT id, ley, numero_articulo, titulo_articulo, materia,
                   chunk_texto,
                   1 - (embedding <=> CAST(:emb AS vector)) AS similitud
            FROM leyes_chunks
            WHERE {where}
            ORDER BY embedding <=> CAST(:emb AS vector)
            LIMIT :top_k
        """),
        params,
    )
    rows = result.fetchall()

    return [
        {
            "fuente":           "leyes_chunks",
            "id":               r[0],
            "ley":              r[1],
            "numero_articulo":  r[2],
            "titulo_articulo":  r[3],
            "materia":          r[4],
            "extracto":         r[5][:300],
            "chunk_texto":      r[5],
            "similitud":        round(float(r[6]), 4),
        }
        for r in rows
    ]
