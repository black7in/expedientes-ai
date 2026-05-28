from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.recuperacion_service import RecuperacionRequest, RecuperacionResult, recuperar

router = APIRouter(prefix="/recuperacion", tags=["recuperacion"])


@router.post("", response_model=RecuperacionResult)
async def recuperacion(
    body: RecuperacionRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await recuperar(body, db)
    except Exception as e:
        print(f"[recuperacion] error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Error interno en recuperación de insumos.")
