"""
indexar_plantillas.py — Carga masiva de memoriales jurídicos como plantillas.

Procesa todos los .pdf y .docx de una carpeta:
  - Extrae HTML semántico (negritas, encabezados preservados)
  - LLM anonimiza y clasifica el memorial
  - Genera embedding del resumen_caso
  - Guarda en plantillas_memoriales

Uso (dentro del contenedor ai):
    python scripts/indexar_plantillas.py /ruta/carpeta/memoriales

Desde fuera:
    docker compose -f docker-compose.dev.yml exec ai \
        python scripts/indexar_plantillas.py /app/recursos/memoriales
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

import asyncpg
import mammoth
import fitz
import anthropic
import openai as openai_sdk
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

# ── Configuración ─────────────────────────────────────────────────────────────

DATABASE_URL      = os.getenv("DATABASE_URL", "postgresql://laravel:secret@postgres:5432/expedientes_juridicos")
LLM_PROVIDER      = os.getenv("LLM_PROVIDER", "anthropic")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
LLM_BASE_URL      = os.getenv("LLM_BASE_URL", "")
LLM_MODEL         = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
TRANSFORMERS_CACHE= os.getenv("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
MODEL_NAME        = "intfloat/multilingual-e5-large"
ORIGEN            = "caso_real"

EXTENSIONES = {".pdf", ".docx"}
ELEMENTOS_PERMITIDOS = {"h1", "h2", "p", "strong", "em", "ul", "ol", "li"}

# ── Extracción HTML ───────────────────────────────────────────────────────────

def extraer_html_docx(ruta: Path) -> str:
    with open(ruta, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value


def extraer_html_pdf(ruta: Path) -> str:
    doc = fitz.open(str(ruta))
    partes = []
    try:
        for pagina in doc:
            h = pagina.get_text("html")
            if h.strip():
                partes.append(h)
    finally:
        doc.close()
    return "\n".join(partes)


def limpiar_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(True):
        tag.attrs = {}
    for tag in soup.find_all(True):
        if tag.name not in ELEMENTOS_PERMITIDOS:
            tag.unwrap()
    resultado = str(soup)
    resultado = re.sub(r"[ \t]+", " ", resultado)
    resultado = re.sub(r"\n{3,}", "\n\n", resultado)
    return resultado.strip()


def texto_para_hash(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", texto).strip().lower()

# ── LLM ───────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Eres un asistente especializado en procesamiento de documentos legales bolivianos.
Tu tarea es transformar un memorial jurídico en una PLANTILLA reutilizable.

El documento de entrada está en HTML semántico con estos elementos: <h1>, <h2>, <p>, <strong>, <em>, <ul>, <ol>, <li>.
Una PLANTILLA conserva la estructura HTML, el tono y la forma del memorial original, pero
elimina TODA información identificable o específica del caso.

Debes devolver EXCLUSIVAMENTE un objeto JSON válido, sin texto adicional antes
o después. No uses bloques de código markdown. No expliques nada."""


def _build_prompt(html: str) -> str:
    return f"""\
Procesa el siguiente memorial y devuelve un JSON con esta estructura exacta:

{{
  "texto_anonimizado": "...",
  "resumen_caso": "...",
  "tipo_memorial": "...",
  "tipo_memorial_propuesto": null,
  "tipo_accion": "...",
  "tipo_accion_propuesto": null,
  "subtipo": null,
  "materia": "...",
  "instancia": "...",
  "formato": "...",
  "estilo": "...",
  "extension": "...",
  "advertencias": []
}}

REGLAS DE ANONIMIZACIÓN:
El input es HTML semántico. Anonimiza SOLO el contenido de texto dentro de las etiquetas,
SIN modificar ni eliminar ninguna etiqueta HTML.

Ejemplo:
ENTRADA:  <p><strong>PRIMERO.-</strong> En fecha 12 de marzo de 2024, el suscrito Carlos Mendoza...</p>
SALIDA:   <p><strong>PRIMERO.-</strong> En fecha [FECHA], el suscrito [NOMBRE_PARTE]...</p>

Datos a reemplazar: nombres → [NOMBRE_PARTE/DEMANDANTE/DEMANDADO], CI/NIT → [DOCUMENTO_IDENTIDAD],
montos → [MONTO], fechas → [FECHA], direcciones → [DIRECCIÓN],
nros. expediente/escritura → [REF_DOCUMENTAL], empresas → [ENTIDAD],
teléfonos → [TELÉFONO], emails → [EMAIL], placas/series → [IDENTIFICADOR]

NO reemplaces: leyes, códigos, términos jurídicos genéricos, instituciones genéricas.

"tipo_memorial": demanda | memorial | apelacion | recurso | contrato | notificacion | otro
"tipo_accion": ejecutiva | coactiva | ordinaria | desalojo | usucapion | terceria | divorcio | nulidad | cumplimiento_contrato | resolucion_contrato | otro
"materia": civil | penal | familiar | laboral | comercial | constitucional | administrativo
"instancia": primera | segunda | casacion | tribunal_agroambiental | otro
"formato": estructurado | corrido | mixto
"estilo": formal | tecnico | mixto
"extension": corta | media | larga

DOCUMENTO:
---
{html}
---"""


async def llamar_llm(html: str) -> dict:
    prompt = _build_prompt(html)
    respuesta_anterior = ""
    ultimo_error = ""

    for intento in range(3):
        user_msg = prompt if intento == 0 else (
            f"Tu respuesta anterior no era JSON válido. Error: {ultimo_error}\n"
            f"Respuesta anterior:\n{respuesta_anterior}\n"
            "Devuelve SOLO el JSON válido, sin markdown."
        )

        if LLM_PROVIDER == "openai":
            kwargs = {"api_key": OPENAI_API_KEY}
            if LLM_BASE_URL:
                kwargs["base_url"] = LLM_BASE_URL
            client = openai_sdk.AsyncOpenAI(**kwargs)
            resp = await client.chat.completions.create(
                model=LLM_MODEL,
                max_tokens=8192,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.1,
            )
            raw = resp.choices[0].message.content.strip()
        else:
            kwargs = {"api_key": ANTHROPIC_API_KEY}
            if LLM_BASE_URL:
                kwargs["base_url"] = LLM_BASE_URL
            client = anthropic.AsyncAnthropic(**kwargs)
            msg = await client.messages.create(
                model=LLM_MODEL,
                max_tokens=8192,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
                temperature=0.1,
            )
            bloque = next((b for b in msg.content if hasattr(b, "text")), None)
            raw = bloque.text.strip() if bloque else ""

        respuesta_anterior = raw
        try:
            return _extraer_json(raw)
        except (json.JSONDecodeError, ValueError) as e:
            ultimo_error = str(e)
            if intento == 2:
                raise RuntimeError(f"LLM no devolvió JSON válido tras 3 intentos: {ultimo_error}")

    raise RuntimeError("Error inesperado en llamar_llm")


def _extraer_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise

# ── DB ────────────────────────────────────────────────────────────────────────

async def hash_existe(conn, content_hash: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM plantillas_memoriales WHERE content_hash = $1", content_hash
    )
    return row is not None


async def guardar_plantilla(conn, llm_result: dict, content_hash: str, emb_str: str) -> str:
    plantilla_id = str(uuid.uuid4())
    advertencias_criticas = {"anonimizacion_dudosa", "clasificacion_incierta", "contenido_sensible"}
    advertencias = llm_result.get("advertencias", [])
    hay_propuesta = llm_result["tipo_memorial"] == "otro" or llm_result["tipo_accion"] == "otro"
    activo = not advertencias_criticas.intersection(advertencias) and not hay_propuesta

    await conn.execute("""
        INSERT INTO plantillas_memoriales (
            id, content_hash, contenido, resumen_caso, embedding,
            tipo_memorial, tipo_memorial_propuesto,
            tipo_accion,   tipo_accion_propuesto,
            subtipo, materia, instancia,
            formato, estilo, extension, origen, calidad, activo
        ) VALUES (
            $1::uuid, $2, $3, $4, $5::vector,
            $6, $7, $8, $9,
            $10, $11, $12, $13, $14, $15, $16, 'estandar', $17
        )
    """,
        plantilla_id, content_hash,
        llm_result["texto_anonimizado"], llm_result["resumen_caso"], emb_str,
        llm_result["tipo_memorial"], llm_result.get("tipo_memorial_propuesto"),
        llm_result["tipo_accion"],   llm_result.get("tipo_accion_propuesto"),
        llm_result.get("subtipo"), llm_result["materia"], llm_result["instancia"],
        llm_result["formato"], llm_result.get("estilo"), llm_result.get("extension"),
        ORIGEN, activo,
    )
    return plantilla_id

# ── Procesar un archivo ───────────────────────────────────────────────────────

async def procesar(ruta: Path, modelo, conn) -> dict:
    # 1. Extraer HTML
    try:
        if ruta.suffix.lower() == ".pdf":
            html_raw = extraer_html_pdf(ruta)
        else:
            html_raw = extraer_html_docx(ruta)
        html = limpiar_html(html_raw)
    except Exception as e:
        return {"ok": False, "error": f"Extracción fallida: {e}"}

    if len(html.strip()) < 500:
        return {"ok": False, "error": "Documento demasiado corto (<500 chars)"}

    # 2. Deduplicación
    content_hash = hashlib.sha256(texto_para_hash(html).encode()).hexdigest()
    if await hash_existe(conn, content_hash):
        return {"ok": False, "error": "Duplicado — ya indexado"}

    # 3. LLM
    try:
        llm_result = await llamar_llm(html)
    except Exception as e:
        return {"ok": False, "error": f"LLM: {e}"}

    # 4. Embedding
    resumen = llm_result.get("resumen_caso", "")
    vector = modelo.encode(f"passage: {resumen}", normalize_embeddings=True).tolist()
    emb_str = "[" + ",".join(map(str, vector)) + "]"

    # 5. Guardar
    try:
        await guardar_plantilla(conn, llm_result, content_hash, emb_str)
    except Exception as e:
        return {"ok": False, "error": f"DB: {e}"}

    return {
        "ok":           True,
        "tipo_memorial": llm_result["tipo_memorial"],
        "tipo_accion":   llm_result["tipo_accion"],
        "materia":       llm_result["materia"],
        "advertencias":  llm_result.get("advertencias", []),
    }

# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/indexar_plantillas.py <carpeta>")
        sys.exit(1)

    carpeta = Path(sys.argv[1])
    if not carpeta.is_dir():
        print(f"Error: '{carpeta}' no es un directorio")
        sys.exit(1)

    archivos = sorted(f for f in carpeta.iterdir() if f.suffix.lower() in EXTENSIONES)
    if not archivos:
        print(f"No se encontraron .pdf ni .docx en {carpeta}")
        sys.exit(1)

    print(f"\n=== INDEXADOR MASIVO DE PLANTILLAS ===")
    print(f"Carpeta : {carpeta}")
    print(f"Archivos: {len(archivos)}")
    print(f"Provider: {LLM_PROVIDER} / {LLM_MODEL}\n")

    print("Cargando modelo de embeddings...")
    modelo = SentenceTransformer(MODEL_NAME, cache_folder=TRANSFORMERS_CACHE)
    print("Modelo listo\n")

    db_url = DATABASE_URL.replace("postgresql://", "postgresql://", 1)
    conn = await asyncpg.connect(db_url)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    resultados = {"ok": [], "error": []}

    for i, ruta in enumerate(archivos, 1):
        print(f"[{i}/{len(archivos)}] {ruta.name}")
        res = await procesar(ruta, modelo, conn)
        if res["ok"]:
            adv = f" ⚠ {res['advertencias']}" if res["advertencias"] else ""
            print(f"  ✓ {res['tipo_memorial']} / {res['tipo_accion']} / {res['materia']}{adv}")
            resultados["ok"].append(ruta.name)
        else:
            print(f"  ✗ {res['error']}")
            resultados["error"].append({"archivo": ruta.name, "error": res["error"]})

    await conn.close()

    print(f"\n=== RESUMEN ===")
    print(f"  Exitosos: {len(resultados['ok'])}")
    print(f"  Errores : {len(resultados['error'])}")
    if resultados["error"]:
        print("\nArchivos con error:")
        for r in resultados["error"]:
            print(f"  - {r['archivo']}: {r['error']}")


if __name__ == "__main__":
    asyncio.run(main())
