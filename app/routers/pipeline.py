from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.analisis_service import AnalisisError, analizar_hechos
from ..services.generacion_service import GeneracionError, GeneracionRequest, GeneracionResult, generar
from ..services.recuperacion_service import RecuperacionRequest, RecuperacionResult, recuperar

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class PipelineRequest(BaseModel):
    narracion: str
    incluir_jurisprudencia: bool = False


class PipelineResponse(BaseModel):
    analisis: dict
    recuperacion: RecuperacionResult
    generacion: GeneracionResult


@router.post("/completo", response_model=PipelineResponse)
async def pipeline_completo(
    body: PipelineRequest,
    db: AsyncSession = Depends(get_db),
):
    analisis = await analizar_hechos(body.narracion, db)

    recuperacion = await recuperar(
        RecuperacionRequest(
            analisis=analisis,
            incluir_jurisprudencia=body.incluir_jurisprudencia,
        ),
        db,
    )

    try:
        resultado_gen = await generar(GeneracionRequest(analisis=analisis, recuperacion=recuperacion))
    except GeneracionError as e:
        print(f"[pipeline] {e.codigo}: {e.detalle}")
        raise HTTPException(
            status_code=500,
            detail={"error": e.codigo, "mensaje": "Error al generar el memorial."},
        )

    return PipelineResponse(
        analisis=analisis.model_dump(),
        recuperacion=recuperacion,
        generacion=resultado_gen,
    )
