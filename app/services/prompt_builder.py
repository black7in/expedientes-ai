from .context_normalizer import ContextoJuridico


def construir_prompt_usuario(contexto: ContextoJuridico, fragmentos: dict) -> str:
    """
    Construye el prompt de usuario que se envía al LLM junto con el system prompt.
    Combina el contexto del caso con los fragmentos recuperados por el RAG.

    Orden deliberado:
      1. Datos del caso (qué escribir)
      2. Hechos e instrucciones del abogado
      3. Ejemplos de estilo (cómo escribirlo)
      4. Artículos legales (con qué fundamento)
      5. Jurisprudencia (qué citar)
    """

    # ── Fragmentos de estilo del estudio ─────────────────────────────────────
    estilo_txt = ""
    if fragmentos.get("docs_estudio"):
        estilo_txt = "\n\n### EJEMPLOS DE ESTILO DEL ESTUDIO\n"
        estilo_txt += "Usa exactamente estas fórmulas y estructura en el documento:\n"
        for doc in fragmentos["docs_estudio"]:
            estilo_txt += f"\n---\n{doc['texto'][:800]}\n"

    # ── Artículos de leyes bolivianas ─────────────────────────────────────────
    leyes_txt = ""
    if fragmentos.get("leyes"):
        leyes_txt = "\n\n### ARTÍCULOS LEGALES APLICABLES\n"
        leyes_txt += "Cítalos en la fundamentación jurídica cuando corresponda:\n"
        for ley in fragmentos["leyes"]:
            titulo = f" — {ley['titulo']}" if ley.get("titulo") else ""
            leyes_txt += f"\n**{ley['ley']}, Art. {ley['articulo']}{titulo}:**\n{ley['texto'][:600]}\n"

    # ── Jurisprudencia TSJ Bolivia ────────────────────────────────────────────
    juris_txt = ""
    if fragmentos.get("jurisprudencia"):
        juris_txt = "\n\n### JURISPRUDENCIA RELEVANTE\n"
        juris_txt += "Cita el número de Auto Supremo exacto al referenciarla:\n"
        for j in fragmentos["jurisprudencia"]:
            fecha = f" ({j['fecha']})" if j.get("fecha") else ""
            juris_txt += f"\n**{j['auto']}{fecha}:**\n{j['texto'][:600]}\n"

    # ── Actuaciones del expediente (solo modo A) ──────────────────────────────
    actuaciones_txt = ""
    if contexto.actuaciones:
        actuaciones_txt = "\n\nActuaciones recientes del expediente:\n"
        for act in contexto.actuaciones:
            actuaciones_txt += (
                f"- [{act.get('tipo', '')}] {act.get('fecha', '')}: "
                f"{act.get('descripcion', '')}\n"
            )

    # ── Instrucciones adicionales del abogado ─────────────────────────────────
    instrucciones_txt = ""
    if contexto.instrucciones_extra:
        instrucciones_txt = (
            f"\n\n**Instrucciones adicionales del abogado:**\n{contexto.instrucciones_extra}"
        )

    prompt = f"""## DATOS DEL CASO

- **Tipo de documento:** {contexto.tipo_documento.upper()}
- **Demandante:** {contexto.demandante or "No especificado"}
- **Demandado:** {contexto.demandado or "No especificado"}
- **Juzgado:** {contexto.juzgado or "No especificado"}
- **Ciudad:** {contexto.ciudad}
- **Tipo de proceso:** {contexto.tipo_proceso or "No especificado"}
- **Etapa procesal:** {contexto.etapa_procesal or "No especificada"}

## HECHOS DEL CASO
{contexto.hechos or "El abogado completará esta sección."}
{actuaciones_txt}{instrucciones_txt}{estilo_txt}{leyes_txt}{juris_txt}

---

Redacta el {contexto.tipo_documento} completo siguiendo la estructura procesal civil boliviana."""

    return prompt.strip()
