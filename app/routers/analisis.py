from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.analisis_service import AnalisisError, analizar_hechos

router = APIRouter(prefix="/analisis", tags=["analisis"])

_MENSAJE_ERROR = (
    "No fue posible estructurar el caso con la información proporcionada. "
    "Por favor amplía el contexto utilizando terminología jurídica precisa: "
    "tipo de relación, fechas, montos, partes involucradas y pretensión específica."
)


class AnalisisRequest(BaseModel):
    narracion: str


@router.post("/hechos")
async def analisis_hechos(
    body: AnalisisRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        analisis = await analizar_hechos(body.narracion, db)
        return {"analisis": analisis.model_dump()}
    except AnalisisError as e:
        # Código interno para observabilidad — no se expone al cliente
        print(f"[analisis] {e.codigo}: {e.detalle}")
        raise HTTPException(
            status_code=422,
            detail={"error": "ANALISIS_INSUFICIENTE", "mensaje": _MENSAJE_ERROR},
        )
