import os
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Documento
from ..services.extractor import extraer_texto
from ..config import settings

router = APIRouter(prefix="/api/documentos", tags=["documentos"])


@router.post("/{documento_id}/extraer")
async def extraer_documento(documento_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Documento).where(Documento.id == _uuid.UUID(documento_id))
    )
    doc = result.scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    if doc.estado_extraccion == "procesado":
        return {"status": "ya_procesado", "documento_id": documento_id}

    ruta = os.path.join(settings.storage_path, doc.nombre_archivo)

    try:
        resultado = extraer_texto(ruta, doc.formato)
        doc.texto_extraido = resultado
        doc.estado_extraccion = "procesado"
    except FileNotFoundError as e:
        doc.estado_extraccion = "error"
        doc.texto_extraido = {"error": str(e)}
    except Exception as e:
        doc.estado_extraccion = "error"
        doc.texto_extraido = {"error": f"Error de extracción: {str(e)}"}

    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(doc)

    return {
        "status": doc.estado_extraccion,
        "documento_id": documento_id,
        "paginas": doc.texto_extraido.get("paginas") if doc.texto_extraido else None,
    }


@router.get("/health")
async def health():
    return {"status": "ok"}
