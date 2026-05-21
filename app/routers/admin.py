import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..services.indexar_service import indexar_auto_supremo, indexar_ley_desde_pdf

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/rag-stats")
async def rag_stats(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT
            (SELECT COUNT(*)              FROM leyes_chunks)            AS leyes_chunks,
            (SELECT COUNT(DISTINCT ley)   FROM leyes_chunks)            AS leyes_count,
            (SELECT COUNT(*)              FROM jurisprudencia_chunks)   AS juris_chunks,
            (SELECT COUNT(DISTINCT numero_auto) FROM jurisprudencia_chunks) AS autos_count,
            (SELECT COUNT(*)              FROM doc_chunks)              AS doc_chunks,
            (SELECT COUNT(DISTINCT documento_id) FROM doc_chunks)       AS docs_count
    """))
    row = result.fetchone()

    leyes_result = await db.execute(text("""
        SELECT ley, materia, COUNT(*) AS chunks
        FROM leyes_chunks
        GROUP BY ley, materia
        ORDER BY ley
    """))
    leyes = leyes_result.fetchall()

    autos_result = await db.execute(text("""
        SELECT numero_auto, fecha_resolucion, materia, sala, COUNT(*) AS chunks
        FROM jurisprudencia_chunks
        GROUP BY numero_auto, fecha_resolucion, materia, sala
        ORDER BY fecha_resolucion DESC NULLS LAST
        LIMIT 50
    """))
    autos = autos_result.fetchall()

    return {
        "stats": {
            "leyes_chunks":  row[0],
            "leyes_count":   row[1],
            "juris_chunks":  row[2],
            "autos_count":   row[3],
            "doc_chunks":    row[4],
            "docs_count":    row[5],
        },
        "leyes": [
            {"ley": r[0], "materia": r[1], "chunks": r[2]}
            for r in leyes
        ],
        "autos": [
            {
                "numero_auto": r[0],
                "fecha":       str(r[1]) if r[1] else None,
                "materia":     r[2],
                "sala":        r[3],
                "chunks":      r[4],
            }
            for r in autos
        ],
    }


@router.post("/indexar-ley")
async def indexar_ley(
    archivo:    UploadFile = File(...),
    nombre_ley: str        = Form(...),
    materia:    str        = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if not archivo.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Solo se aceptan archivos PDF")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(await archivo.read())
        tmp_path = tmp.name

    try:
        chunks = await indexar_ley_desde_pdf(tmp_path, nombre_ley, materia, db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        os.unlink(tmp_path)

    return {"ok": True, "ley": nombre_ley, "chunks": chunks}


class IndexarAutoRequest(BaseModel):
    numero_auto: str
    texto:       str
    materia:     str
    fecha:       Optional[str] = None
    sala:        Optional[str] = None


@router.post("/indexar-jurisprudencia")
async def indexar_jurisprudencia(req: IndexarAutoRequest, db: AsyncSession = Depends(get_db)):
    if len(req.texto.strip()) < 50:
        raise HTTPException(status_code=422, detail="El texto del Auto Supremo es demasiado corto")

    chunks = await indexar_auto_supremo(
        numero_auto=req.numero_auto,
        fecha=req.fecha,
        materia=req.materia,
        sala=req.sala,
        texto=req.texto,
        db=db,
    )
    return {"ok": True, "numero_auto": req.numero_auto, "chunks": chunks}


# ── PLANTILLAS DE DOCUMENTOS ───────────────────────────────────────────────────

class PlantillaRequest(BaseModel):
    nombre:         str
    tipo_documento: str
    materia:        str = "civil"
    contenido:      str
    activo:         bool = True
    es_default:     bool = False


class PlantillaUpdateRequest(BaseModel):
    nombre:     Optional[str]  = None
    contenido:  Optional[str]  = None
    activo:     Optional[bool] = None
    es_default: Optional[bool] = None


@router.get("/plantillas")
async def listar_plantillas(materia: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    if materia:
        query = text("""
            SELECT id, nombre, tipo_documento, materia,
                   activo, es_default, created_at,
                   LENGTH(contenido) AS contenido_len
            FROM plantillas_documentos
            WHERE activo = true AND materia = :materia
            ORDER BY materia, tipo_documento, created_at DESC
        """)
        result = await db.execute(query, {"materia": materia})
    else:
        query = text("""
            SELECT id, nombre, tipo_documento, materia,
                   activo, es_default, created_at,
                   LENGTH(contenido) AS contenido_len
            FROM plantillas_documentos
            WHERE activo = true
            ORDER BY materia, tipo_documento, created_at DESC
        """)
        result = await db.execute(query)

    rows = result.fetchall()
    return [
        {
            "id":             str(r[0]),
            "nombre":         r[1],
            "tipo_documento": r[2],
            "materia":        r[3],
            "activo":         r[4],
            "es_default":     r[5],
            "created_at":     str(r[6]),
            "contenido_len":  r[7],
        }
        for r in rows
    ]


@router.get("/plantillas/{plantilla_id}")
async def obtener_plantilla(plantilla_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            SELECT id, nombre, tipo_documento, materia, contenido, activo, es_default, created_at
            FROM plantillas_documentos
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": plantilla_id},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    return {
        "id":             str(row[0]),
        "nombre":         row[1],
        "tipo_documento": row[2],
        "materia":        row[3],
        "contenido":      row[4],
        "activo":         row[5],
        "es_default":     row[6],
        "created_at":     str(row[7]),
    }


@router.post("/plantillas", status_code=201)
async def crear_plantilla(req: PlantillaRequest, db: AsyncSession = Depends(get_db)):
    if not req.contenido.strip():
        raise HTTPException(status_code=422, detail="El contenido no puede estar vacío")

    new_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    await db.execute(
        text("""
            INSERT INTO plantillas_documentos
                (id, nombre, tipo_documento, materia, contenido, activo, es_default, created_at, updated_at)
            VALUES
                (CAST(:id AS uuid), :nombre, :tipo, :materia, :contenido,
                 :activo, :es_default, :now, :now)
        """),
        {
            "id":         new_id,
            "nombre":     req.nombre,
            "tipo":       req.tipo_documento,
            "materia":    req.materia,
            "contenido":  req.contenido,
            "activo":     req.activo,
            "es_default": req.es_default,
            "now":        now,
        },
    )
    await db.commit()
    return {"ok": True, "id": new_id}


@router.put("/plantillas/{plantilla_id}")
async def actualizar_plantilla(
    plantilla_id: str, req: PlantillaUpdateRequest, db: AsyncSession = Depends(get_db)
):
    updates = {}
    if req.nombre is not None:
        updates["nombre"] = req.nombre
    if req.contenido is not None:
        updates["contenido"] = req.contenido
    if req.activo is not None:
        updates["activo"] = req.activo
    if req.es_default is not None:
        updates["es_default"] = req.es_default

    if not updates:
        raise HTTPException(status_code=422, detail="No hay campos para actualizar")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = plantilla_id

    await db.execute(
        text(f"""
            UPDATE plantillas_documentos
            SET {set_clause}, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        updates,
    )
    await db.commit()
    return {"ok": True}


@router.post("/plantillas/{plantilla_id}/default")
async def set_plantilla_default(plantilla_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT materia, tipo_documento FROM plantillas_documentos WHERE id = CAST(:id AS uuid)"),
        {"id": plantilla_id},
    )
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")

    materia, tipo = row[0], row[1]

    await db.execute(
        text("""
            UPDATE plantillas_documentos
            SET es_default = false, updated_at = NOW()
            WHERE materia = :materia AND tipo_documento = :tipo
        """),
        {"materia": materia, "tipo": tipo},
    )
    await db.execute(
        text("""
            UPDATE plantillas_documentos
            SET es_default = true, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": plantilla_id},
    )
    await db.commit()
    return {"ok": True, "default_id": plantilla_id}


@router.delete("/plantillas/{plantilla_id}")
async def eliminar_plantilla(plantilla_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("""
            UPDATE plantillas_documentos
            SET activo = false, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": plantilla_id},
    )
    await db.commit()
    return {"ok": True}
