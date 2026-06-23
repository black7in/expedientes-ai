from fastapi import APIRouter, HTTPException

from ..services.generacion_service import GeneracionError, GeneracionRequest, GeneracionResult, generar

router = APIRouter(prefix="/generacion", tags=["generacion"])


@router.post("", response_model=GeneracionResult)
async def generacion(body: GeneracionRequest):
    try:
        return await generar(body)
    except GeneracionError as e:
        print(f"[generacion] {e.codigo}: {e.detalle}")
        raise HTTPException(
            status_code=500,
            detail={"error": e.codigo, "mensaje": "Error al generar el memorial. Intenta nuevamente."},
        )
