import uuid as _uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Documento
from ..services.ner_service import ner_service, ROLES_PER

router = APIRouter(prefix="/api/documentos", tags=["ner"])


@router.post("/{documento_id}/ner")
async def ejecutar_ner(documento_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Documento).where(Documento.id == _uuid.UUID(documento_id))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    texto = (doc.texto_extraido or {}).get("texto_completo", "")
    if not texto.strip():
        raise HTTPException(status_code=422, detail="Documento sin texto extraído")

    entidades = ner_service.extraer_entidades(texto)
    texto_anon = ner_service.anonimizar(texto, entidades)

    doc.entidades         = entidades
    doc.texto_anonimizado = texto_anon
    doc.estado_extraccion = "pendiente_revision"
    doc.updated_at        = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return {
        "status":       "pendiente_revision",
        "documento_id": documento_id,
        "entidades":    len(entidades),
    }


class EntidadPatch(BaseModel):
    tipo:       Literal["PER", "CI", "NIT", "TEL", "DIR"] | None = None
    confirmado: bool | None = None
    eliminar:   bool = False
    rol:        str | None = None
    inicio:     int | None = None
    fin:        int | None = None
    texto:      str | None = None


@router.patch("/{documento_id}/entidades/{entidad_id}")
async def actualizar_entidad(
    documento_id: str,
    entidad_id:   str,
    body:         EntidadPatch,
    db:           AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Documento).where(Documento.id == _uuid.UUID(documento_id))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    entidades: list[dict] = list(doc.entidades or [])
    idx = next((i for i, e in enumerate(entidades) if e["id"] == entidad_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Entidad no encontrada")

    if body.eliminar:
        entidades.pop(idx)
    else:
        if body.tipo is not None:
            entidades[idx]["tipo"] = body.tipo
            entidades[idx].pop("rol", None)
        if body.confirmado is not None:
            entidades[idx]["confirmado"] = body.confirmado
        if body.rol is not None and entidades[idx].get("tipo") == "PER":
            entidades[idx]["rol"] = body.rol if body.rol in ROLES_PER else None
            entidades[idx]["confirmado"] = True
        if body.inicio is not None and body.fin is not None and body.texto is not None:
            texto_doc = (doc.texto_extraido or {}).get("texto_completo", "")
            if 0 <= body.inicio < body.fin <= len(texto_doc):
                entidades[idx]["inicio"] = body.inicio
                entidades[idx]["fin"]    = body.fin
                entidades[idx]["texto"]  = body.texto
                entidades[idx]["confirmado"] = True

    texto = (doc.texto_extraido or {}).get("texto_completo", "")
    doc.entidades         = ner_service.recompute_placeholders(entidades)
    doc.texto_anonimizado = ner_service.regenerar_anonimizado(texto, entidades)
    doc.updated_at        = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return {"status": "ok", "entidades": doc.entidades}


@router.post("/{documento_id}/confirmar")
async def confirmar_anonimizacion(documento_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Documento).where(Documento.id == _uuid.UUID(documento_id))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    if doc.estado_extraccion != "pendiente_revision":
        raise HTTPException(
            status_code=422,
            detail=f"Estado actual '{doc.estado_extraccion}' no permite confirmación",
        )

    doc.estado_extraccion = "confirmado"
    doc.updated_at        = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return {
        "status":        "confirmado",
        "documento_id":  documento_id,
        "expediente_id": str(doc.expediente_id) if doc.expediente_id else None,
    }
