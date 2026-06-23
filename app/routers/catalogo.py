from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

router = APIRouter(prefix="/api/catalogo", tags=["catalogo"])

_CAMPOS_VALIDOS = {"tipo_memorial", "tipo_accion"}


class AprobarBody(BaseModel):
    valor_oficial: Optional[str] = None
    decidido_por: Optional[str] = "admin"


class RechazarBody(BaseModel):
    motivo: Optional[str] = None
    decidido_por: Optional[str] = "admin"


@router.get("/propuestas")
async def listar_propuestas(
    estado: str = "pendiente",
    db: AsyncSession = Depends(get_db),
):
    if estado not in ("pendiente", "aprobada", "rechazada"):
        raise HTTPException(status_code=422, detail="estado debe ser: pendiente | aprobada | rechazada")

    result = await db.execute(
        text("""
            SELECT id, campo, valor_propuesto, frecuencia, estado,
                   doc_ids, fecha_creacion, fecha_decision, decidido_por
            FROM catalogo_propuestas
            WHERE estado = :estado
            ORDER BY frecuencia DESC, fecha_creacion ASC
        """),
        {"estado": estado},
    )
    rows = result.fetchall()
    return [_propuesta_to_dict(r) for r in rows]


@router.post("/propuestas/{propuesta_id}/aprobar")
async def aprobar_propuesta(
    propuesta_id: str,
    body: AprobarBody,
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        text("""
            SELECT id, campo, valor_propuesto, doc_ids
            FROM catalogo_propuestas
            WHERE id = CAST(:id AS uuid) AND estado = 'pendiente'
        """),
        {"id": propuesta_id},
    )
    propuesta = row.fetchone()
    if not propuesta:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada o ya fue decidida.")

    prop_id, campo, valor_propuesto, doc_ids = propuesta

    if campo not in _CAMPOS_VALIDOS:
        raise HTTPException(status_code=500, detail=f"Campo inválido en BD: '{campo}'")

    valor_oficial = (body.valor_oficial or "").strip() or valor_propuesto
    decidido_por = (body.decidido_por or "admin").strip()
    campo_propuesto = f"{campo}_propuesto"

    await db.execute(
        text("""
            UPDATE catalogo_propuestas
            SET estado = 'aprobada',
                fecha_decision = now(),
                decidido_por = :decidido_por
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": propuesta_id, "decidido_por": decidido_por},
    )

    # campo y campo_propuesto son de whitelist — no hay riesgo de inyección
    await db.execute(
        text(f"""
            UPDATE plantillas_memoriales
            SET {campo}           = :valor_oficial,
                {campo_propuesto} = NULL
            WHERE id = ANY(:doc_ids)
              AND {campo} = 'otro'
        """),
        {"valor_oficial": valor_oficial, "doc_ids": doc_ids},
    )

    await db.commit()

    return {
        "ok": True,
        "propuesta_id": propuesta_id,
        "campo": campo,
        "valor_aprobado": valor_oficial,
        "moldes_actualizados": len(doc_ids) if doc_ids else 0,
    }


@router.post("/propuestas/{propuesta_id}/rechazar")
async def rechazar_propuesta(
    propuesta_id: str,
    body: RechazarBody,
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(
        text("""
            SELECT id, campo, valor_propuesto, doc_ids
            FROM catalogo_propuestas
            WHERE id = CAST(:id AS uuid) AND estado = 'pendiente'
        """),
        {"id": propuesta_id},
    )
    propuesta = row.fetchone()
    if not propuesta:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada o ya fue decidida.")

    _, campo, valor_propuesto, doc_ids = propuesta
    decidido_por = (body.decidido_por or "admin").strip()

    await db.execute(
        text("""
            UPDATE catalogo_propuestas
            SET estado = 'rechazada',
                fecha_decision = now(),
                decidido_por = :decidido_por
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": propuesta_id, "decidido_por": decidido_por},
    )

    await db.commit()

    return {
        "ok": True,
        "propuesta_id": propuesta_id,
        "campo": campo,
        "valor_rechazado": valor_propuesto,
        "moldes_en_cuarentena": len(doc_ids) if doc_ids else 0,
    }


def _propuesta_to_dict(r) -> dict:
    return {
        "id":              str(r[0]),
        "campo":           r[1],
        "valor_propuesto": r[2],
        "frecuencia":      r[3],
        "estado":          r[4],
        "doc_ids":         [str(d) for d in r[5]] if r[5] else [],
        "fecha_creacion":  str(r[6]),
        "fecha_decision":  str(r[7]) if r[7] else None,
        "decidido_por":    r[8],
    }
