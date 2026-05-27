import asyncio
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

    texto_actual = (doc.texto_extraido or {}).get("texto_completo", "").strip()
    if doc.estado_extraccion == "procesado" and texto_actual:
        return {"status": "ya_procesado", "documento_id": documento_id}

    ruta = os.path.join(settings.storage_path, doc.nombre_archivo)

    try:
        # Extracción CPU-bound en thread separado para no bloquear el event loop
        resultado = await asyncio.to_thread(extraer_texto, ruta, doc.formato)
        doc.texto_extraido = resultado
        doc.estado_extraccion = "procesado"
        paginas = resultado.get("paginas")
    except FileNotFoundError as e:
        doc.estado_extraccion = "error"
        doc.texto_extraido = {"error": str(e)}
        paginas = None
    except Exception as e:
        doc.estado_extraccion = "error"
        doc.texto_extraido = {"error": f"Error de extracción: {str(e)}"}
        paginas = None

    # Columna es TIMESTAMP WITHOUT TIME ZONE → asyncpg no acepta tz-aware
    doc.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return {
        "status":       doc.estado_extraccion,
        "documento_id": documento_id,
        "paginas":      paginas,
    }


@router.get("/health")
async def health():
    return {"status": "ok"}
