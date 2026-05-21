import json
import time
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from .retriever import buscar_chunks, buscar_con_fallback
from .llm_client import crear_cliente, get_model

_MAX_TOKENS  = 2048
_MAX_RETRIES = 2

_PROMPT_SECCION = """\
Sos un abogado boliviano experto en derecho civil. Redactá la sección "{nombre_seccion}" de una {tipo_documento} {subtipo}.

INSTRUCCIONES ESPECÍFICAS:
{instrucciones}

DATOS DEL CASO:
{datos_caso}

{bloque_demandas}
{bloque_leyes}
REGLAS ESTRICTAS:
1. Usá SOLO los datos del caso entregados. NO inventes nombres, fechas, montos ni documentos.
2. Si falta algún dato necesario, escribí [PENDIENTE: descripción del dato].
3. Citá artículos SOLO si te los entregué en las referencias legales.
4. Seguí el lenguaje formal boliviano (tercera persona, estilo forense).
5. NO uses markdown ni formato especial. Devolvé texto plano.
6. NO incluyas el título de la sección, solo el contenido.

Generá únicamente el contenido de la sección "{nombre_seccion}".\
"""

_BLOQUE_DEMANDAS = """\
REFERENCIAS DE DEMANDAS SIMILARES (estilo y estructura):
{fragmentos}

"""

_BLOQUE_LEYES = """\
ARTÍCULOS LEGALES APLICABLES (citá literalmente cuando corresponda):
{fragmentos}

"""


async def generar_documento(
    generacion_id: str,
    plantilla: dict,
    input_formulario: dict,
    formato_salida: str,
    db: AsyncSession,
) -> dict:
    """
    Orquesta la generación completa de un documento por secciones (batch).
    Persiste trazabilidad en generacion_secciones.
    Retorna {contenido, tokens_input, tokens_output, secciones_generadas}.
    """
    client = crear_cliente()
    secciones_def = plantilla.get("secciones", [])
    tipo_doc = plantilla.get("tipo_documento", "demanda")
    subtipo  = plantilla.get("subtipo", "")

    partes_documento: list[str] = []
    tokens_input_total  = 0
    tokens_output_total = 0
    secciones_generadas: list[dict] = []

    datos_caso = _formatear_datos_caso(input_formulario)

    for sec_def in secciones_def:
        seccion_id  = sec_def["id"]
        obligatoria = sec_def.get("obligatoria", True)

        # Verificar cancelación entre secciones
        cancelada = await _es_cancelada(generacion_id, db)
        if cancelada:
            await _actualizar_estado(generacion_id, "cancelada", db)
            break

        try:
            template = sec_def.get("salida", {}).get(formato_salida, "{contenido}")

            # Sección estática: el template no usa {contenido} → no llamar al LLM
            if "{contenido}" not in template:
                partes_documento.append(template)
                secciones_generadas.append({
                    "seccion_id": seccion_id,
                    "nombre":     sec_def.get("nombre", seccion_id),
                    "tokens":     0,
                })
                continue

            # 1. Retrieval específico de la sección
            chunks_seccion = await _recuperar_chunks_seccion(
                sec_def, input_formulario, tipo_doc, subtipo, db
            )

            # 2. Construir prompt
            prompt = _construir_prompt_seccion(
                sec_def, tipo_doc, subtipo, datos_caso, chunks_seccion
            )

            # 3. Llamar al LLM con reintentos
            contenido, tok_in, tok_out = await _llamar_llm_con_retry(
                client, prompt, seccion_id
            )

            # 4. Aplicar template de salida
            parte_renderizada = template.format(contenido=contenido)
            partes_documento.append(parte_renderizada)

            tokens_input_total  += tok_in
            tokens_output_total += tok_out

            # 5. Persistir trazabilidad
            await _guardar_seccion(
                generacion_id=generacion_id,
                seccion_id=seccion_id,
                orden=sec_def.get("orden", 0),
                contenido=contenido,
                chunks=chunks_seccion,
                prompt=prompt,
                tok_in=tok_in,
                tok_out=tok_out,
                db=db,
            )

            secciones_generadas.append({
                "seccion_id": seccion_id,
                "nombre":     sec_def.get("nombre", seccion_id),
                "tokens":     tok_in + tok_out,
            })

        except Exception as e:
            if obligatoria:
                await _actualizar_estado(generacion_id, "error", db)
                raise RuntimeError(f"Error en sección obligatoria '{seccion_id}': {e}") from e
            # Sección opcional fallida: continuar sin ella
            secciones_generadas.append({
                "seccion_id": seccion_id,
                "nombre":     sec_def.get("nombre", seccion_id),
                "error":      str(e),
            })

    contenido_final = "\n\n".join(partes_documento)

    # Actualizar generacion con resultado final
    await db.execute(
        text("""
            UPDATE generaciones SET
                estado           = 'completado',
                contenido_actual = :contenido,
                llm_model        = :modelo,
                tokens_input     = :tok_in,
                tokens_output    = :tok_out,
                updated_at       = NOW()
            WHERE id = CAST(:id AS uuid)
        """),
        {
            "contenido": contenido_final,
            "modelo":    get_model(),
            "tok_in":    tokens_input_total,
            "tok_out":   tokens_output_total,
            "id":        generacion_id,
        },
    )
    await db.commit()

    return {
        "contenido":          contenido_final,
        "tokens_input":       tokens_input_total,
        "tokens_output":      tokens_output_total,
        "secciones":          secciones_generadas,
    }


async def regenerar_seccion(
    generacion_id: str,
    seccion_id: str,
    plantilla: dict,
    input_formulario: dict,
    formato_salida: str,
    feedback: str | None,
    db: AsyncSession,
) -> dict:
    """Regenera una sección individual y actualiza el documento ensamblado."""
    client = crear_cliente()
    tipo_doc = plantilla.get("tipo_documento", "demanda")
    subtipo  = plantilla.get("subtipo", "")

    sec_def = next((s for s in plantilla["secciones"] if s["id"] == seccion_id), None)
    if not sec_def:
        raise ValueError(f"Sección '{seccion_id}' no existe en la plantilla")

    datos_caso = _formatear_datos_caso(input_formulario)
    chunks_seccion = await _recuperar_chunks_seccion(sec_def, input_formulario, tipo_doc, subtipo, db)
    prompt = _construir_prompt_seccion(sec_def, tipo_doc, subtipo, datos_caso, chunks_seccion)

    if feedback:
        # Cargar contenido anterior para incluirlo en el prompt de regeneración
        result = await db.execute(
            text("""
                SELECT contenido_generado FROM generacion_secciones
                WHERE generacion_id = CAST(:gid AS uuid) AND seccion_id = :sid
                ORDER BY created_at DESC LIMIT 1
            """),
            {"gid": generacion_id, "sid": seccion_id},
        )
        row = result.fetchone()
        contenido_anterior = row[0] if row else ""

        prompt += f"""

INTENTO ANTERIOR (no fue satisfactorio):
{contenido_anterior}

FEEDBACK DEL USUARIO:
{feedback}

Regenerá la sección teniendo en cuenta el feedback."""

    contenido, tok_in, tok_out = await _llamar_llm_con_retry(client, prompt, seccion_id)

    template = sec_def.get("salida", {}).get(formato_salida, "{contenido}")
    parte_renderizada = template.format(contenido=contenido)

    # Actualizar sección existente
    await db.execute(
        text("""
            UPDATE generacion_secciones SET
                contenido_generado = :contenido,
                contenido_editado  = NULL,
                chunks_usados      = CAST(:chunks AS jsonb),
                regenerada_count   = regenerada_count + 1,
                tokens_input       = :tok_in,
                tokens_output      = :tok_out,
                updated_at         = NOW()
            WHERE generacion_id = CAST(:gid AS uuid) AND seccion_id = :sid
        """),
        {
            "contenido": contenido,
            "chunks":    json.dumps({"chunks": _chunks_a_trazabilidad(chunks_seccion)}, ensure_ascii=False),
            "tok_in":    tok_in,
            "tok_out":   tok_out,
            "gid":       generacion_id,
            "sid":       seccion_id,
        },
    )

    # Rearmar el documento completo reemplazando esta sección
    await _rearmar_documento(generacion_id, plantilla, formato_salida, db)
    await db.commit()

    return {"seccion_id": seccion_id, "contenido": contenido, "parte": parte_renderizada}


# ── Helpers privados ──────────────────────────────────────────────────────────

async def _recuperar_chunks_seccion(
    sec_def: dict,
    input_formulario: dict,
    tipo_doc: str,
    subtipo: str,
    db: AsyncSession,
) -> list[dict]:
    """Ejecuta retrieval según la config de la sección."""
    retrieval_cfg = sec_def.get("retrieval", {})
    fuentes = retrieval_cfg.get("fuentes", [])
    seccion_id = sec_def["id"]

    # Query = instrucciones de la sección + hechos del formulario
    hechos = input_formulario.get("hechos", "")
    query = f"{sec_def.get('nombre', seccion_id)} {tipo_doc} {subtipo} {hechos[:500]}"

    todos_los_chunks: list[dict] = []

    FUENTES_SOPORTADAS = {"doc_chunks", "leyes_chunks"}

    for fuente_cfg in fuentes:
        fuente  = fuente_cfg["tipo"]
        top_k   = fuente_cfg.get("top_k", 3)
        filtros = dict(fuente_cfg.get("filtros", {}))

        if fuente not in FUENTES_SOPORTADAS:
            # jurisprudencia_chunks, doctrina_chunks → futuro, ignorar por ahora
            continue

        if fuente == "doc_chunks":
            filtros.setdefault("tipo_doc", tipo_doc)
            filtros.setdefault("seccion", seccion_id)
            chunks, _ = await buscar_con_fallback(query, filtros, top_k, db=db)
        else:
            chunks = await buscar_chunks(query, fuente, filtros, top_k, db=db)

        todos_los_chunks.extend(chunks)

    return todos_los_chunks


def _construir_prompt_seccion(
    sec_def: dict,
    tipo_doc: str,
    subtipo: str,
    datos_caso: str,
    chunks: list[dict],
) -> str:
    doc_chunks  = [c for c in chunks if c["fuente"] == "doc_chunks"]
    ley_chunks  = [c for c in chunks if c["fuente"] == "leyes_chunks"]

    bloque_demandas = ""
    if doc_chunks:
        fragmentos = "\n---\n".join(c["chunk_texto"] for c in doc_chunks)
        bloque_demandas = _BLOQUE_DEMANDAS.format(fragmentos=fragmentos)

    bloque_leyes = ""
    if ley_chunks:
        fragmentos = "\n---\n".join(
            f"Art. {c['numero_articulo']} {c.get('ley','')}: {c['chunk_texto']}"
            for c in ley_chunks
        )
        bloque_leyes = _BLOQUE_LEYES.format(fragmentos=fragmentos)

    return _PROMPT_SECCION.format(
        nombre_seccion=sec_def.get("nombre", sec_def["id"]),
        tipo_documento=tipo_doc,
        subtipo=subtipo,
        instrucciones=sec_def.get("instrucciones", ""),
        datos_caso=datos_caso,
        bloque_demandas=bloque_demandas,
        bloque_leyes=bloque_leyes,
    )


def _formatear_datos_caso(input_formulario: dict) -> str:
    lineas = []
    mapeo = {
        "demandante":   "Demandante",
        "demandado":    "Demandado",
        "juzgado":      "Juzgado",
        "ciudad":       "Ciudad",
        "tipo_proceso": "Tipo de proceso",
        "subtipo":      "Subtipo de demanda",
        "hechos":       "Hechos del caso",
        "monto":        "Monto reclamado",
        "instrucciones_extra": "Instrucciones adicionales",
    }
    for campo, etiqueta in mapeo.items():
        valor = input_formulario.get(campo, "")
        if valor:
            lineas.append(f"- {etiqueta}: {valor}")
    return "\n".join(lineas) if lineas else "(sin datos adicionales)"


async def _llamar_llm_con_retry(
    client,
    prompt: str,
    seccion_id: str,
) -> tuple[str, int, int]:
    ultimo_error = ""
    for intento in range(_MAX_RETRIES):
        try:
            msg = await client.messages.create(
                model=get_model(),
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            bloque = next((b for b in msg.content if hasattr(b, "text")), None)
            if bloque is None:
                raise RuntimeError("El LLM no devolvió bloque de texto")
            contenido = bloque.text.strip()
            return contenido, msg.usage.input_tokens, msg.usage.output_tokens
        except Exception as e:
            ultimo_error = str(e)
            if intento == _MAX_RETRIES - 1:
                raise RuntimeError(f"LLM falló en sección '{seccion_id}': {ultimo_error}")
    raise RuntimeError("Error inesperado en LLM")


async def _guardar_seccion(
    generacion_id: str,
    seccion_id: str,
    orden: int,
    contenido: str,
    chunks: list[dict],
    prompt: str,
    tok_in: int,
    tok_out: int,
    db: AsyncSession,
) -> None:
    await db.execute(
        text("""
            INSERT INTO generacion_secciones
                (generacion_id, seccion_id, orden, contenido_generado,
                 chunks_usados, prompt_usado, tokens_input, tokens_output)
            VALUES
                (CAST(:gid AS uuid), :sid, :orden, :contenido,
                 CAST(:chunks AS jsonb), :prompt, :tok_in, :tok_out)
        """),
        {
            "gid":      generacion_id,
            "sid":      seccion_id,
            "orden":    orden,
            "contenido": contenido,
            "chunks":   json.dumps({"chunks": _chunks_a_trazabilidad(chunks)}, ensure_ascii=False),
            "prompt":   prompt,
            "tok_in":   tok_in,
            "tok_out":  tok_out,
        },
    )


def _chunks_a_trazabilidad(chunks: list[dict]) -> list[dict]:
    result = []
    for c in chunks:
        item = {
            "fuente":    c["fuente"],
            "id":        c.get("id"),
            "similitud": c.get("similitud"),
            "extracto":  c.get("extracto", ""),
        }
        if c["fuente"] == "doc_chunks":
            item["seccion"] = c.get("seccion")
        elif c["fuente"] == "leyes_chunks":
            item["ley"]             = c.get("ley")
            item["numero_articulo"] = c.get("numero_articulo")
        result.append(item)
    return result


async def _es_cancelada(generacion_id: str, db: AsyncSession) -> bool:
    result = await db.execute(
        text("SELECT cancelada FROM generaciones WHERE id = CAST(:id AS uuid)"),
        {"id": generacion_id},
    )
    row = result.fetchone()
    return bool(row[0]) if row else False


async def _actualizar_estado(generacion_id: str, estado: str, db: AsyncSession) -> None:
    await db.execute(
        text("UPDATE generaciones SET estado = :estado, updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
        {"estado": estado, "id": generacion_id},
    )
    await db.commit()


async def _rearmar_documento(
    generacion_id: str,
    plantilla: dict,
    formato_salida: str,
    db: AsyncSession,
) -> None:
    """Reconstruye contenido_actual en base a las secciones actuales."""
    result = await db.execute(
        text("""
            SELECT seccion_id, COALESCE(contenido_editado, contenido_generado)
            FROM generacion_secciones
            WHERE generacion_id = CAST(:id AS uuid)
            ORDER BY orden
        """),
        {"id": generacion_id},
    )
    rows = result.fetchall()

    secciones_map = {sec["id"]: sec for sec in plantilla["secciones"]}
    partes = []
    for seccion_id, contenido in rows:
        sec_def = secciones_map.get(seccion_id, {})
        template = sec_def.get("salida", {}).get(formato_salida, "{contenido}")
        partes.append(template.format(contenido=contenido))

    await db.execute(
        text("UPDATE generaciones SET contenido_actual = :contenido, updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
        {"contenido": "\n\n".join(partes), "id": generacion_id},
    )
