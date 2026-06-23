import asyncio
from typing import List, Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .analisis_service import AnalisisCaso
from .embedding_service import EmbeddingService

_SIMILITUD_MINIMA_LEYES = 0.5


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class Molde(BaseModel):
    doc_id: str
    contenido: str
    formato: str
    score_promedio: Optional[float] = None
    similitud: float


class ArticuloLey(BaseModel):
    ley: str
    numero_articulo: str
    titulo_articulo: Optional[str] = None
    chunk_texto: str
    similitud: float


class RecuperacionRequest(BaseModel):
    analisis: AnalisisCaso
    incluir_jurisprudencia: bool = False


class RecuperacionResult(BaseModel):
    molde: Optional[Molde] = None
    leyes: List[ArticuloLey] = []
    leyes_procesales: List[ArticuloLey] = []
    jurisprudencia: Optional[str] = None
    advertencias: List[str] = []


# ---------------------------------------------------------------------------
# Helpers de embedding (CPU-bound → executor para no bloquear el event loop)
# ---------------------------------------------------------------------------

async def _embed(texto: str) -> list[float]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, EmbeddingService.encode_query, texto)


# ---------------------------------------------------------------------------
# Retriever de moldes
# ---------------------------------------------------------------------------

# Niveles de fallback: (cláusula WHERE adicional, advertencia a emitir)
_FALLBACK_LEVELS = [
    (
        "tipo_memorial = :tm AND tipo_accion = :ta AND materia = :mat AND instancia = :ins",
        None,
    ),
    (
        "tipo_memorial = :tm AND tipo_accion = :ta AND materia = :mat",
        "molde_fallback_usado",
    ),
    (
        "tipo_memorial = :tm AND materia = :mat",
        "molde_fallback_usado",
    ),
    (
        "tipo_memorial = :tm",
        "molde_fallback_usado",
    ),
]


async def _retriever_moldes(
    analisis: AnalisisCaso,
    vector: list[float],
    db: AsyncSession,
) -> tuple[Optional[Molde], list[str]]:
    vector_str = EmbeddingService.to_pgvector_str(vector)
    cls = analisis.clasificacion

    params = {
        "tm":     cls.tipo_memorial,
        "ta":     cls.tipo_accion,
        "mat":    cls.materia,
        "ins":    cls.instancia,
        "vector": vector_str,
    }

    for where, advertencia in _FALLBACK_LEVELS:
        result = await db.execute(
            text(f"""
                SELECT id::text, contenido, formato, score_promedio,
                       1 - (embedding <=> CAST(:vector AS vector)) AS similitud
                FROM plantillas_memoriales
                WHERE activo = true AND {where}
                ORDER BY
                    CASE calidad WHEN 'premium' THEN 1 WHEN 'estandar' THEN 2 ELSE 3 END,
                    embedding <=> CAST(:vector AS vector),
                    score_promedio DESC NULLS LAST,
                    usos_totales DESC
                LIMIT 1
            """),
            params,
        )
        row = result.fetchone()
        if not row:
            continue

        molde = Molde(
            doc_id=row[0],
            contenido=row[1],
            formato=row[2],
            score_promedio=float(row[3]) if row[3] is not None else None,
            similitud=float(row[4]),
        )

        await db.execute(
            text("""
                UPDATE plantillas_memoriales
                SET usos_totales = usos_totales + 1
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": molde.doc_id},
        )
        await db.commit()

        return molde, ([advertencia] if advertencia else [])

    return None, ["sin_molde_disponible"]


# ---------------------------------------------------------------------------
# Retriever de leyes
# ---------------------------------------------------------------------------

async def _retriever_leyes(
    analisis: AnalisisCaso,
    vector: list[float],
    db: AsyncSession,
) -> tuple[List[ArticuloLey], list[str]]:
    vector_str = EmbeddingService.to_pgvector_str(vector)

    result = await db.execute(
        text("""
            SELECT ley, numero_articulo, titulo_articulo, chunk_texto,
                   1 - (embedding <=> CAST(:vector AS vector)) AS similitud
            FROM leyes_chunks
            WHERE materia = :materia
            ORDER BY embedding <=> CAST(:vector AS vector)
            LIMIT 10
        """),
        {"vector": vector_str, "materia": analisis.clasificacion.materia},
    )
    rows = result.fetchall()

    leyes = [
        ArticuloLey(
            ley=r[0],
            numero_articulo=r[1],
            titulo_articulo=r[2],
            chunk_texto=r[3],
            similitud=float(r[4]),
        )
        for r in rows
        if float(r[4]) >= _SIMILITUD_MINIMA_LEYES
    ]

    advertencias = ["sin_leyes_relevantes"] if not leyes else []
    return leyes, advertencias


_MATERIA_PROCESAL: dict[str, str] = {
    "civil":    "procesal_civil",
    "penal":    "procesal_penal",
    "laboral":  "procesal_laboral",
    "familiar": "procesal_civil",
}


async def _retriever_leyes_procesales(
    analisis: AnalisisCaso,
    vector: list[float],
    db: AsyncSession,
) -> tuple[List[ArticuloLey], list[str]]:
    materia_procesal = _MATERIA_PROCESAL.get(analisis.clasificacion.materia)
    if not materia_procesal:
        return [], []

    vector_str = EmbeddingService.to_pgvector_str(vector)
    result = await db.execute(
        text("""
            SELECT ley, numero_articulo, titulo_articulo, chunk_texto,
                   1 - (embedding <=> CAST(:vector AS vector)) AS similitud
            FROM leyes_chunks
            WHERE materia = :materia
            ORDER BY embedding <=> CAST(:vector AS vector)
            LIMIT 3
        """),
        {"vector": vector_str, "materia": materia_procesal},
    )
    rows = result.fetchall()

    leyes = [
        ArticuloLey(
            ley=r[0],
            numero_articulo=r[1],
            titulo_articulo=r[2],
            chunk_texto=r[3],
            similitud=float(r[4]),
        )
        for r in rows
        if float(r[4]) >= _SIMILITUD_MINIMA_LEYES
    ]
    return leyes, []


# ---------------------------------------------------------------------------
# Sistema de jurisprudencia (stub — implementación futura)
# ---------------------------------------------------------------------------

async def _sistema_juris(analisis: AnalisisCaso) -> Optional[str]:
    # TODO: llamar al sistema externo de jurisprudencia (Autos Supremos TSJ)
    return None


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def recuperar(req: RecuperacionRequest, db: AsyncSession) -> RecuperacionResult:
    # Computar embeddings en paralelo (CPU-bound en executor)
    texto_moldes = req.analisis.resumen_caso_query
    texto_leyes = (
        f"{req.analisis.resumen_caso_query}. "
        f"Términos: {', '.join(req.analisis.keywords_legales)}"
    )

    vector_moldes, vector_leyes = await asyncio.gather(
        _embed(texto_moldes),
        _embed(texto_leyes),
    )

    # Retrievers de BD secuenciales (AsyncSession no es seguro para uso concurrente)
    advertencias: list[str] = []

    try:
        molde, adv = await _retriever_moldes(req.analisis, vector_moldes, db)
        advertencias.extend(adv)
    except Exception as e:
        print(f"[recuperacion] retriever_moldes falló: {e}")
        molde = None
        advertencias.append("sin_molde_disponible")

    try:
        leyes, adv = await _retriever_leyes(req.analisis, vector_leyes, db)
        advertencias.extend(adv)
    except Exception as e:
        print(f"[recuperacion] retriever_leyes falló: {e}")
        leyes = []
        advertencias.append("sin_leyes_relevantes")

    try:
        leyes_procesales, _ = await _retriever_leyes_procesales(req.analisis, vector_leyes, db)
    except Exception as e:
        print(f"[recuperacion] retriever_leyes_procesales falló: {e}")
        leyes_procesales = []

    # Jurisprudencia (llamada externa — timeout de 60s)
    jurisprudencia: Optional[str] = None
    if req.incluir_jurisprudencia:
        try:
            jurisprudencia = await asyncio.wait_for(_sistema_juris(req.analisis), timeout=60.0)
            if jurisprudencia is None:
                advertencias.append("jurisprudencia_no_disponible")
        except Exception as e:
            print(f"[recuperacion] sistema_juris falló: {e}")
            advertencias.append("jurisprudencia_no_disponible")

    return RecuperacionResult(
        molde=molde,
        leyes=leyes,
        leyes_procesales=leyes_procesales,
        jurisprudencia=jurisprudencia,
        advertencias=advertencias,
    )
