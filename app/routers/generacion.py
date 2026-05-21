import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.generator import generar_documento, regenerar_seccion
from ..services.exporter import exportar_a_docx

router = APIRouter(prefix="/api/generaciones", tags=["generacion"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerarRequest(BaseModel):
    usuario_id:     str
    plantilla_id:   str
    formato_salida: str = "estructurado"   # 'estructurado' | 'corrido'
    expediente_id:  Optional[str] = None

    # Datos del caso (formulario manual)
    demandante:          Optional[str] = None
    demandado:           Optional[str] = None
    juzgado:             Optional[str] = None
    ciudad:              str = "Santa Cruz de la Sierra"
    tipo_proceso:        Optional[str] = None
    subtipo:             Optional[str] = None
    hechos:              Optional[str] = None
    monto:               Optional[str] = None
    instrucciones_extra: Optional[str] = None


class RegenerarRequest(BaseModel):
    feedback: Optional[str] = None


class EditarContenidoRequest(BaseModel):
    contenido: str


class EditarSeccionRequest(BaseModel):
    contenido_editado: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def crear_generacion(req: GenerarRequest, db: AsyncSession = Depends(get_db)):
    """
    Crea una generación nueva y la procesa de forma batch (sección por sección).
    Bloquea hasta que termina (~30-90s según cantidad de secciones).
    """
    # 1. Cargar plantilla
    plantilla = await _cargar_plantilla(req.plantilla_id, db)

    # 2. Validar input mínimo
    _validar_input(req)

    # 3. Preparar input_formulario
    input_formulario = {
        "demandante":          req.demandante or "",
        "demandado":           req.demandado or "",
        "juzgado":             req.juzgado or "",
        "ciudad":              req.ciudad,
        "tipo_proceso":        req.tipo_proceso or "",
        "subtipo":             req.subtipo or plantilla.get("subtipo", ""),
        "hechos":              req.hechos or "",
        "monto":               req.monto or "",
        "instrucciones_extra": req.instrucciones_extra or "",
    }

    # 4. Crear registro en estado 'generando'
    generacion_id = str(uuid.uuid4())
    await db.execute(
        text("""
            INSERT INTO generaciones
                (id, usuario_id, expediente_id, tipo_documento, subtipo,
                 plantilla_id, formato_salida, input_formulario, estado, llm_provider)
            VALUES
                (CAST(:id AS uuid), CAST(:uid AS uuid), CAST(:exp AS uuid),
                 :tipo_doc, :subtipo,
                 CAST(:plt_id AS uuid), :formato, CAST(:input AS jsonb),
                 'generando', 'anthropic')
        """),
        {
            "id":      generacion_id,
            "uid":     req.usuario_id,
            "exp":     req.expediente_id,
            "tipo_doc": plantilla["tipo_documento"],
            "subtipo":  plantilla["subtipo"],
            "plt_id":  req.plantilla_id,
            "formato": req.formato_salida,
            "input":   json.dumps(input_formulario, ensure_ascii=False),
        },
    )
    await db.commit()

    # 5. Generar (bloquea hasta completar)
    try:
        resultado = await generar_documento(
            generacion_id=generacion_id,
            plantilla=plantilla,
            input_formulario=input_formulario,
            formato_salida=req.formato_salida,
            db=db,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "generacion_id":  generacion_id,
        "estado":         "completado",
        "contenido":      resultado["contenido"],
        "tokens_input":   resultado["tokens_input"],
        "tokens_output":  resultado["tokens_output"],
        "secciones":      resultado["secciones"],
    }


@router.get("")
async def listar_generaciones(usuario_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, tipo_documento, subtipo, formato_salida,
                   estado, tokens_input, tokens_output, created_at
            FROM generaciones
            WHERE usuario_id = CAST(:uid AS uuid)
            ORDER BY created_at DESC
            LIMIT 50
        """),
        {"uid": usuario_id},
    )
    rows = result.fetchall()
    return [
        {
            "id":             str(r[0]),
            "tipo_documento": r[1],
            "subtipo":        r[2],
            "formato_salida": r[3],
            "estado":         r[4],
            "tokens_total":   (r[5] or 0) + (r[6] or 0),
            "created_at":     str(r[7]),
        }
        for r in rows
    ]


@router.get("/{generacion_id}")
async def obtener_generacion(generacion_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, usuario_id, expediente_id, tipo_documento, subtipo,
                   plantilla_id, formato_salida, input_formulario,
                   estado, cancelada, contenido_actual,
                   llm_model, tokens_input, tokens_output, created_at, updated_at
            FROM generaciones
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": generacion_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Generación no encontrada")

    return {
        "id":              str(row[0]),
        "usuario_id":      str(row[1]),
        "expediente_id":   str(row[2]) if row[2] else None,
        "tipo_documento":  row[3],
        "subtipo":         row[4],
        "plantilla_id":    str(row[5]) if row[5] else None,
        "formato_salida":  row[6],
        "input_formulario": row[7],
        "estado":          row[8],
        "cancelada":       row[9],
        "contenido_actual": row[10],
        "llm_model":       row[11],
        "tokens_input":    row[12],
        "tokens_output":   row[13],
        "created_at":      str(row[14]),
        "updated_at":      str(row[15]),
    }


@router.get("/{generacion_id}/secciones")
async def listar_secciones(generacion_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, seccion_id, orden,
                   contenido_generado, contenido_editado,
                   chunks_usados, tokens_input, tokens_output, regenerada_count
            FROM generacion_secciones
            WHERE generacion_id = CAST(:id AS uuid)
            ORDER BY orden
        """),
        {"id": generacion_id},
    )
    rows = result.fetchall()
    return [
        {
            "id":                 str(r[0]),
            "seccion_id":         r[1],
            "orden":              r[2],
            "contenido_generado": r[3],
            "contenido_editado":  r[4],
            "chunks_usados":      r[5],
            "tokens_input":       r[6],
            "tokens_output":      r[7],
            "regenerada_count":   r[8],
        }
        for r in rows
    ]


@router.post("/{generacion_id}/secciones/{seccion_id}/regenerar")
async def regenerar(
    generacion_id: str,
    seccion_id: str,
    req: RegenerarRequest,
    db: AsyncSession = Depends(get_db),
):
    gen = await _cargar_generacion_completa(generacion_id, db)
    plantilla = await _cargar_plantilla(str(gen["plantilla_id"]), db)

    try:
        resultado = await regenerar_seccion(
            generacion_id=generacion_id,
            seccion_id=seccion_id,
            plantilla=plantilla,
            input_formulario=gen["input_formulario"],
            formato_salida=gen["formato_salida"],
            feedback=req.feedback,
            db=db,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    return resultado


@router.put("/{generacion_id}")
async def actualizar_contenido(
    generacion_id: str,
    req: EditarContenidoRequest,
    db: AsyncSession = Depends(get_db),
):
    """Guarda edición manual del documento completo."""
    await db.execute(
        text("""
            UPDATE generaciones SET
                contenido_actual = :contenido,
                estado           = 'editado',
                updated_at       = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {"contenido": req.contenido, "id": generacion_id},
    )
    await db.commit()
    return {"ok": True}


@router.put("/{generacion_id}/secciones/{seccion_id}")
async def editar_seccion(
    generacion_id: str,
    seccion_id: str,
    req: EditarSeccionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Guarda edición manual de una sección específica."""
    await db.execute(
        text("""
            UPDATE generacion_secciones SET
                contenido_editado = :contenido,
                updated_at        = NOW()
            WHERE generacion_id = CAST(:gid AS uuid) AND seccion_id = :sid
        """),
        {"contenido": req.contenido_editado, "gid": generacion_id, "sid": seccion_id},
    )
    await db.commit()
    return {"ok": True}


@router.post("/{generacion_id}/cancelar")
async def cancelar(generacion_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("""
            UPDATE generaciones SET cancelada = TRUE, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": generacion_id},
    )
    await db.commit()
    return {"ok": True}


@router.get("/{generacion_id}/descargar")
async def descargar_docx(generacion_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT contenido_actual, tipo_documento, subtipo FROM generaciones WHERE id = CAST(:id AS uuid)"),
        {"id": generacion_id},
    )
    row = result.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Generación sin contenido")

    docx_bytes = exportar_a_docx(row[0])
    nombre = f"{row[1]}_{row[2]}_{generacion_id[:8]}.docx"

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={nombre}"},
    )


@router.delete("/{generacion_id}")
async def eliminar(generacion_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("DELETE FROM generaciones WHERE id = CAST(:id AS uuid)"),
        {"id": generacion_id},
    )
    await db.commit()
    return {"ok": True}


# ── Plantillas (consulta) ─────────────────────────────────────────────────────

@router.get("/plantillas/listar")
async def listar_plantillas(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, tipo_documento, subtipo, nombre, descripcion, version, activa
            FROM plantillas
            WHERE activa = TRUE
            ORDER BY tipo_documento, subtipo
        """)
    )
    rows = result.fetchall()
    return [
        {
            "id":             str(r[0]),
            "tipo_documento": r[1],
            "subtipo":        r[2],
            "nombre":         r[3],
            "descripcion":    r[4],
            "version":        r[5],
            "activa":         r[6],
        }
        for r in rows
    ]


# ── Helpers privados ──────────────────────────────────────────────────────────

async def _cargar_plantilla(plantilla_id: str, db: AsyncSession) -> dict:
    result = await db.execute(
        text("SELECT id, tipo_documento, subtipo, nombre, secciones FROM plantillas WHERE id = CAST(:id AS uuid)"),
        {"id": plantilla_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return {
        "id":             str(row[0]),
        "tipo_documento": row[1],
        "subtipo":        row[2],
        "nombre":         row[3],
        "secciones":      row[4] if isinstance(row[4], list) else json.loads(row[4]),
    }


async def _cargar_generacion_completa(generacion_id: str, db: AsyncSession) -> dict:
    result = await db.execute(
        text("SELECT plantilla_id, formato_salida, input_formulario FROM generaciones WHERE id = CAST(:id AS uuid)"),
        {"id": generacion_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Generación no encontrada")
    return {
        "plantilla_id":    row[0],
        "formato_salida":  row[1],
        "input_formulario": row[2],
    }


def _validar_input(req: GenerarRequest) -> None:
    errores = []
    if not req.demandante or len(req.demandante.strip()) < 3:
        errores.append("demandante: requerido (mínimo 3 caracteres)")
    if not req.demandado or len(req.demandado.strip()) < 3:
        errores.append("demandado: requerido (mínimo 3 caracteres)")
    if not req.hechos or len(req.hechos.strip()) < 50:
        errores.append("hechos: requerido (mínimo 50 caracteres)")
    if req.formato_salida not in ("estructurado", "corrido"):
        errores.append("formato_salida: debe ser 'estructurado' o 'corrido'")
    if errores:
        raise HTTPException(status_code=422, detail={"errores": errores})
