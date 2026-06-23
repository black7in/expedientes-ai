import uuid as _uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import llm_client

_SYSTEM = """Eres un asistente jurídico boliviano especializado en síntesis de expedientes.
Tu tarea es generar un resumen conciso y útil del expediente basándote en sus documentos anonimizados.

REGLAS:
1. Usa EXCLUSIVAMENTE la información de los documentos proporcionados.
2. Mantén los placeholders tal cual aparecen: [DEMANDANTE], [DEMANDADO], [CI_1], etc.
3. El resumen debe cubrir: hechos principales, partes involucradas, pretensiones y estado del caso.
4. Sé conciso (máximo 600 palabras). No repitas información.
5. Redacta en español jurídico boliviano formal."""


def _construir_prompt(documentos: list[dict], resumen_previo: str | None) -> str:
    bloques = []
    for i, doc in enumerate(documentos, 1):
        bloques.append(
            f"[Documento {i}: {doc['nombre_archivo']} — {doc['tipo_documento']}]\n"
            f"{doc['texto_anonimizado']}"
        )

    partes = "\n\n".join(bloques)

    if resumen_previo:
        return (
            f"RESUMEN PREVIO DEL EXPEDIENTE:\n{resumen_previo}\n\n"
            f"DOCUMENTOS CONFIRMADOS DEL EXPEDIENTE:\n{partes}\n\n"
            "Genera un resumen actualizado que integre toda la información anterior."
        )

    return (
        f"DOCUMENTOS CONFIRMADOS DEL EXPEDIENTE:\n{partes}\n\n"
        "Genera un resumen del expediente con los documentos proporcionados."
    )


async def generar_resumen_expediente(expediente_id: str, db: AsyncSession) -> str:
    exp_uuid = _uuid.UUID(expediente_id)

    docs_result = await db.execute(
        text("""
            SELECT nombre_archivo, tipo_documento, texto_anonimizado
            FROM documentos
            WHERE expediente_id = :eid
              AND estado_extraccion = 'confirmado'
              AND texto_anonimizado IS NOT NULL
            ORDER BY created_at
        """),
        {"eid": exp_uuid},
    )
    documentos = [
        {"nombre_archivo": r[0], "tipo_documento": r[1], "texto_anonimizado": r[2]}
        for r in docs_result.fetchall()
    ]

    if not documentos:
        return ""

    resumen_result = await db.execute(
        text("SELECT resumen_anonimizado FROM expedientes WHERE id = :eid"),
        {"eid": exp_uuid},
    )
    row = resumen_result.fetchone()
    resumen_previo = row[0] if row else None

    prompt = _construir_prompt(documentos, resumen_previo)
    resumen = await llm_client.completar(
        system=_SYSTEM,
        user=prompt,
        max_tokens=2048,
        temperature=0.1,
    )

    await db.execute(
        text("UPDATE expedientes SET resumen_anonimizado = :r WHERE id = :eid"),
        {"r": resumen, "eid": exp_uuid},
    )
    await db.commit()

    return resumen
