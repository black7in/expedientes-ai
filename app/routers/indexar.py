from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.indexar_service import indexar_documento

router = APIRouter(prefix="/api", tags=["indexar"])


class IndexarRequest(BaseModel):
    tipo_doc: str   # "demanda", "memorial", "contrato", etc.


@router.post("/documentos/{documento_id}/indexar")
def indexar(documento_id: str, req: IndexarRequest, db: Session = Depends(get_db)):
    row = db.execute(
        text("""
            SELECT texto_extraido, expediente_id
            FROM documentos
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": documento_id},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    texto_extraido = row[0]
    if not texto_extraido:
        raise HTTPException(status_code=422, detail="El documento no tiene texto extraído aún")

    texto = texto_extraido.get("texto_completo", "")
    if not texto.strip():
        raise HTTPException(status_code=422, detail="El texto extraído está vacío")

    chunks = indexar_documento(
        documento_id=documento_id,
        texto=texto,
        expediente_id=str(row[1]) if row[1] else None,
        tipo_doc=req.tipo_doc,
        db=db,
    )

    return {"ok": True, "chunks": chunks}
