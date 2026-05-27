import asyncio
import os
import tempfile
import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Documento
from ..config import settings
from ..core.catalogos import ORIGENES
from ..services.plantillas_service import indexar_plantilla, indexar_plantilla_desde_texto
from ..services.extractor import extraer_html

router = APIRouter(prefix="/api/plantillas", tags=["plantillas"])

_FORMATOS_ARCHIVO = ("pdf", "docx", "txt")


@router.post("/indexar", status_code=201)
async def indexar(
    archivo: UploadFile = File(...),
    origen: str = Form("caso_real"),
    db: AsyncSession = Depends(get_db),
):
    if origen not in ORIGENES:
        raise HTTPException(status_code=422, detail=f"origen inválido. Permitidos: {ORIGENES}")

    ext = archivo.filename.lower().rsplit(".", 1)[-1] if "." in archivo.filename else ""
    if ext not in _FORMATOS_ARCHIVO:
        raise HTTPException(status_code=422, detail="Solo se aceptan archivos .pdf, .docx o .txt")

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
        tmp.write(await archivo.read())
        tmp_path = tmp.name

    try:
        resultado = await indexar_plantilla(tmp_path, ext, origen, db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        os.unlink(tmp_path)

    return resultado


@router.post("/desde-documento/{documento_id}", status_code=201)
async def desde_documento(documento_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Documento).where(Documento.id == _uuid.UUID(documento_id))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    texto = (doc.texto_extraido or {}).get("texto_completo", "").strip()

    if not texto:
        ruta = os.path.join(settings.storage_path, doc.nombre_archivo)
        try:
            resultado = await asyncio.to_thread(extraer_html, ruta, doc.formato)
            texto = resultado.get("html", "").strip()
        except FileNotFoundError:
            raise HTTPException(status_code=422, detail="El archivo no existe en storage. Extrae el texto primero.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error al extraer texto: {e}")

    try:
        resultado = await indexar_plantilla_desde_texto(texto, "caso_real", db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return resultado


@router.get("")
async def listar(
    tipo_memorial: str | None = None,
    tipo_accion:   str | None = None,
    materia:       str | None = None,
    instancia:     str | None = None,
    activo:        bool = True,
    db: AsyncSession = Depends(get_db),
):
    conditions = ["activo = :activo"]
    params: dict = {"activo": activo}

    if tipo_memorial:
        conditions.append("tipo_memorial = :tipo_memorial")
        params["tipo_memorial"] = tipo_memorial
    if tipo_accion:
        conditions.append("tipo_accion = :tipo_accion")
        params["tipo_accion"] = tipo_accion
    if materia:
        conditions.append("materia = :materia")
        params["materia"] = materia
    if instancia:
        conditions.append("instancia = :instancia")
        params["instancia"] = instancia

    where = " AND ".join(conditions)
    result = await db.execute(
        text(f"""
            SELECT id, tipo_memorial, tipo_accion, subtipo, materia, instancia,
                   formato, estilo, extension, origen, calidad, activo,
                   score_promedio, usos_totales, tasa_aceptacion,
                   resumen_caso, fecha_indexacion
            FROM plantillas_memoriales
            WHERE {where}
            ORDER BY
                CASE calidad WHEN 'premium' THEN 0 WHEN 'estandar' THEN 1 ELSE 2 END,
                fecha_indexacion DESC
            LIMIT 100
        """),
        params,
    )
    rows = result.fetchall()
    return [_row_to_dict(r) for r in rows]


@router.get("/{plantilla_id}")
async def detalle(plantilla_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, tipo_memorial, tipo_accion, subtipo, materia, instancia,
                   formato, estilo, extension, origen, calidad, activo,
                   score_promedio, usos_totales, tasa_aceptacion,
                   resumen_caso, contenido, fecha_indexacion
            FROM plantillas_memoriales
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": plantilla_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return {**_row_to_dict(row), "contenido": row[16]}


@router.put("/{plantilla_id}/activar")
async def toggle_activo(plantilla_id: str, activo: bool, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(
        text("SELECT 1 FROM plantillas_memoriales WHERE id = CAST(:id AS uuid)"),
        {"id": plantilla_id},
    )
    if not exists.fetchone():
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    await db.execute(
        text("UPDATE plantillas_memoriales SET activo = :activo WHERE id = CAST(:id AS uuid)"),
        {"activo": activo, "id": plantilla_id},
    )
    await db.commit()
    return {"ok": True, "id": plantilla_id, "activo": activo}


def _row_to_dict(r) -> dict:
    return {
        "id":               str(r[0]),
        "tipo_memorial":    r[1],
        "tipo_accion":      r[2],
        "subtipo":          r[3],
        "materia":          r[4],
        "instancia":        r[5],
        "formato":          r[6],
        "estilo":           r[7],
        "extension":        r[8],
        "origen":           r[9],
        "calidad":          r[10],
        "activo":           r[11],
        "score_promedio":   r[12],
        "usos_totales":     r[13],
        "tasa_aceptacion":  r[14],
        "resumen_caso":     r[15],
        "fecha_indexacion": str(r[16]),
    }
