from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.resumen_service import generar_resumen_expediente

router = APIRouter(prefix="/api/expedientes", tags=["expedientes"])


@router.post("/{expediente_id}/resumir")
async def resumir_expediente(expediente_id: str, db: AsyncSession = Depends(get_db)):
    try:
        resumen = await generar_resumen_expediente(expediente_id, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al generar resumen: {e}")

    if not resumen:
        raise HTTPException(
            status_code=422,
            detail="No hay documentos confirmados en este expediente",
        )

    return {"status": "ok", "expediente_id": expediente_id, "chars": len(resumen)}
