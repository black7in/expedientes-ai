import json
import time
import uuid
from typing import Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..prompts.system_prompt_civil import SYSTEM_PROMPT_CIVIL  # fallback
from ..services.context_normalizer import (
    normalizar_desde_expediente,
    normalizar_desde_formulario,
)
from ..services.prompt_builder import construir_prompt_usuario
from ..services.retrieval_service import recuperar_fragmentos

router = APIRouter(prefix="/api", tags=["generacion"])


class GenerarRequest(BaseModel):
    tipo_documento: str
    creado_por: str                          # UUID del usuario Laravel
    instrucciones: Optional[str] = None
    # Modo A — desde expediente existente
    expediente_id: Optional[str] = None
    # Modo B — formulario manual
    demandante: Optional[str] = None
    demandado: Optional[str] = None
    juzgado: Optional[str] = None
    ciudad: Optional[str] = "Santa Cruz de la Sierra"
    tipo_proceso: Optional[str] = None
    hechos: Optional[str] = None


@router.post("/generar")
def generar(req: GenerarRequest, db: Session = Depends(get_db)):
    inicio = time.time()

    # 1. Construir contexto jurídico
    if req.expediente_id:
        try:
            contexto = normalizar_desde_expediente(
                req.expediente_id, req.tipo_documento, req.instrucciones, db
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    else:
        contexto = normalizar_desde_formulario({
            "tipo_documento":    req.tipo_documento,
            "demandante":        req.demandante or "",
            "demandado":         req.demandado or "",
            "juzgado":           req.juzgado or "",
            "ciudad":            req.ciudad or "Santa Cruz de la Sierra",
            "tipo_proceso":      req.tipo_proceso or "",
            "hechos":            req.hechos or "",
            "instrucciones_extra": req.instrucciones,
        })

    # 2. Recuperar fragmentos semánticos relevantes
    query = f"{req.tipo_documento} {contexto.tipo_proceso} {contexto.hechos}"
    fragmentos = recuperar_fragmentos(
        query=query,
        tipo_documento=req.tipo_documento,
        materia=contexto.materia,
        db=db,
    )

    # 3. Construir prompt de usuario
    prompt_usuario = construir_prompt_usuario(contexto, fragmentos)

    # 3b. Obtener system prompt desde DB (plantilla activa default para materia+tipo)
    row = db.execute(
        text("""
            SELECT contenido FROM plantillas_documentos
            WHERE materia = :materia
              AND tipo_documento = :tipo
              AND es_default = true
              AND activo = true
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"materia": contexto.materia, "tipo": req.tipo_documento},
    ).fetchone()
    system_prompt = row.contenido if row else SYSTEM_PROMPT_CIVIL

    # 4. Llamar a Claude API
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt_usuario}],
        )
        borrador = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="ANTHROPIC_API_KEY inválida o no configurada")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Error al llamar a Claude: {str(e)}")

    # 5. Persistir generación en la base de datos
    tiempo_ms = int((time.time() - inicio) * 1000)
    generacion_id = str(uuid.uuid4())

    db.execute(
        text("""
            INSERT INTO generaciones (
                id, expediente_id, creado_por, tipo_documento,
                contexto_usado, chunks_usados, prompt_enviado,
                borrador_generado, modelo_usado, tokens_usados,
                tiempo_ms, created_at, updated_at
            ) VALUES (
                CAST(:id AS uuid),
                CAST(:exp_id AS uuid),
                CAST(:creado_por AS uuid),
                :tipo_doc,
                CAST(:contexto AS jsonb),
                CAST(:chunks AS jsonb),
                :prompt,
                :borrador,
                :modelo,
                :tokens,
                :tiempo_ms,
                NOW(), NOW()
            )
        """),
        {
            "id":         generacion_id,
            "exp_id":     req.expediente_id,
            "creado_por": req.creado_por,
            "tipo_doc":   req.tipo_documento,
            "contexto":   json.dumps(contexto.to_dict(), ensure_ascii=False),
            "chunks":     json.dumps({
                "docs_estudio":  [c["tipo"]     for c in fragmentos.get("docs_estudio", [])],
                "jurisprudencia":[c["auto"]     for c in fragmentos.get("jurisprudencia", [])],
                "leyes":         [c["articulo"] for c in fragmentos.get("leyes", [])],
            }),
            "prompt":     prompt_usuario,
            "borrador":   borrador,
            "modelo":     "claude-sonnet-4-6",
            "tokens":     tokens,
            "tiempo_ms":  tiempo_ms,
        },
    )
    db.commit()

    return {
        "generacion_id": generacion_id,
        "borrador":       borrador,
        "fragmentos_usados": {
            "docs_estudio":  len(fragmentos.get("docs_estudio", [])),
            "jurisprudencia":len(fragmentos.get("jurisprudencia", [])),
            "leyes":         len(fragmentos.get("leyes", [])),
        },
        "tokens_usados": tokens,
        "tiempo_ms":     tiempo_ms,
    }
