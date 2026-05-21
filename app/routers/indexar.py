from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.indexar_service import indexar_documento

router = APIRouter(prefix="/api", tags=["indexar"])


class IndexarRequest(BaseModel):
    tipo_doc: str | None = None


@router.post("/documentos/{documento_id}/indexar")
async def indexar(documento_id: str, req: IndexarRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT texto_extraido, expediente_id
            FROM documentos
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": documento_id},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    texto_extraido = row[0]
    if not texto_extraido:
        raise HTTPException(status_code=422, detail="El documento no tiene texto extraído aún")

    texto = texto_extraido.get("texto_completo", "")
    if not texto.strip():
        raise HTTPException(status_code=422, detail="El texto extraído está vacío")

    try:
        resultado = await indexar_documento(
            documento_id=documento_id,
            texto=texto,
            expediente_id=str(row[1]) if row[1] else None,
            tipo_doc=req.tipo_doc,
            db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, **resultado}
