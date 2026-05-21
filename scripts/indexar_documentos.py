"""
indexar_documentos.py — Carga masiva de documentos legales del estudio en doc_chunks.

Procesa todos los .pdf y .docx de una carpeta, clasificando y parseando
cada documento con LLM antes de generar embeddings y guardar en pgvector.

Uso:
    python scripts/indexar_documentos.py /ruta/a/carpeta/de/docs

Desde el contenedor:
    docker compose exec ai python scripts/indexar_documentos.py /app/docs_estudio

Variables de entorno requeridas:
    DATABASE_URL      — postgresql://user:pass@host/db
    ANTHROPIC_API_KEY — clave de la API de Anthropic
"""

import os
import sys
import json
import hashlib
import unicodedata
import re
from pathlib import Path

import psycopg2
import pdfplumber
import docx
from sentence_transformers import SentenceTransformer
import anthropic

# ── Configuración ─────────────────────────────────────────────────────────────

DATABASE_URL      = os.getenv("DATABASE_URL", "postgresql://laravel:secret@postgres:5432/expedientes_juridicos")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_NAME        = "intfloat/multilingual-e5-large"
CLAUDE_MODEL      = "claude-sonnet-4-6"
EMBEDDING_MODEL   = "multilingual-e5-large"
PARSER_VERSION    = "parser-v1.0"
MAX_CHARS_CHUNK   = 1800   # ~450 tokens, seguro bajo el límite de 512 del E5
INTEGRIDAD_MIN    = 0.85

EXTENSIONES_VALIDAS = {".pdf", ".docx"}
TAMANO_MIN_BYTES    = 1024
TAMANO_MAX_BYTES    = 50 * 1024 * 1024  # 50 MB

# Magic bytes para validar que el contenido coincide con la extensión
MAGIC_BYTES = {
    ".pdf":  b"%PDF",
    ".docx": b"PK\x03\x04",  # ZIP (formato base de DOCX)
}

SECCIONES_VALIDAS = {
    "demanda":     ["encabezado", "tipo_demanda", "generales", "objeto", "hechos",
                    "fundamento_derecho", "jurisprudencia", "doctrina", "petitorio",
                    "otrosi", "cierre", "otro"],
    "sentencia":   ["encabezado", "vistos", "considerandos", "parte_resolutiva", "cierre", "otro"],
    "contrato":    ["encabezado", "partes", "antecedentes", "clausulas", "firmas", "otro"],
    "memorial":    ["encabezado", "objeto", "fundamento", "petitorio", "otrosi", "cierre", "otro"],
    "notificacion":["encabezado", "contenido", "cierre", "otro"],
    "otro":        ["contenido", "otro"],
}

# ── Extracción de texto ───────────────────────────────────────────────────────

def extraer_pdf(ruta: Path) -> str:
    texto = ""
    with pdfplumber.open(ruta) as pdf:
        for pagina in pdf.pages:
            contenido = pagina.extract_text()
            if contenido:
                texto += contenido + "\n"
    return texto


def extraer_docx(ruta: Path) -> str:
    doc = docx.Document(str(ruta))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extraer_texto(ruta: Path) -> str:
    if ruta.suffix.lower() == ".pdf":
        return extraer_pdf(ruta)
    return extraer_docx(ruta)


def validar_archivo(ruta: Path, texto: str) -> tuple[bool, str]:
    tamano = ruta.stat().st_size

    if tamano < TAMANO_MIN_BYTES:
        return False, f"Archivo muy pequeño ({tamano} bytes)"

    if tamano > TAMANO_MAX_BYTES:
        return False, f"Archivo demasiado grande ({tamano / 1024 / 1024:.1f} MB, máx 50 MB)"

    # Magic bytes — verifica que el contenido coincida con la extensión declarada
    magic_esperado = MAGIC_BYTES.get(ruta.suffix.lower(), b"")
    if magic_esperado:
        with open(ruta, "rb") as f:
            cabecera = f.read(4)
        if not cabecera.startswith(magic_esperado):
            return False, f"Magic bytes no coinciden con extensión {ruta.suffix} — archivo corrupto o renombrado"

    if len(texto.strip()) < 500:
        return False, "Texto extraído insuficiente (<500 chars) — puede ser PDF escaneado sin OCR"

    return True, ""

# ── Normalización ─────────────────────────────────────────────────────────────

def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFC", texto)
    texto = re.sub(r"-\n", "", texto)
    texto = re.sub(r"\n\s*\d+\s*\n", "\n", texto)
    texto = re.sub(r" {2,}", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()

# ── LLM ───────────────────────────────────────────────────────────────────────

def _llamar_claude(client: anthropic.Anthropic, prompt: str, max_tokens: int) -> str:
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _extraer_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def clasificar(client: anthropic.Anthropic, texto: str) -> dict:
    prompt = (
        "Sos un clasificador de documentos legales bolivianos.\n"
        "Identificá: tipo_documento (demanda|sentencia|memorial|contrato|notificacion|otro), "
        "subtipo (si es demanda: ejecutiva|coactiva|ordinaria|desalojo|usucapion|terceria|divorcio|nulidad|otro_subtipo), "
        "estructura (corrido|estructurado), confianza (alta|media|baja), razon (1 oración).\n"
        "Devolvé SOLO JSON válido, sin markdown.\n\n"
        f"DOCUMENTO:\n{texto[:2000]}"
    )
    raw = _llamar_claude(client, prompt, max_tokens=256)
    try:
        return _extraer_json(raw)
    except Exception:
        return {"tipo_documento": "otro", "subtipo": None, "estructura": "corrido",
                "confianza": "baja", "razon": "No clasificado"}


def parsear(client: anthropic.Anthropic, texto: str, tipo_documento: str) -> dict:
    secciones = " | ".join(SECCIONES_VALIDAS.get(tipo_documento, ["contenido", "otro"]))
    prompt = (
        f"Sos un parser de documentos legales bolivianos. Dividí el {tipo_documento} en secciones.\n"
        f"Secciones permitidas: {secciones}\n"
        "REGLAS: NO modifiques el texto. Cada otrosi es entrada separada. "
        "Extraé metadata si aparece (monto, moneda, departamento, partes, instrumento_base).\n"
        "Devolvé SOLO JSON válido, sin markdown.\n"
        'Estructura: {"tipo_documento":"...","subtipo":null,"estructura":"corrido","metadata":{},'
        '"secciones":[{"tipo":"encabezado","texto":"...","orden":1}]}\n\n'
        f"DOCUMENTO:\n{texto}"
    )

    for intento in range(3):
        raw = _llamar_claude(client, prompt, max_tokens=8192)
        try:
            resultado = _extraer_json(raw)
            resultado.setdefault("tipo_documento", tipo_documento)
            resultado.setdefault("subtipo", None)
            resultado.setdefault("estructura", "corrido")
            resultado.setdefault("metadata", {})
            resultado.setdefault("secciones", [])
            return resultado
        except Exception as e:
            if intento == 2:
                raise RuntimeError(f"Parser falló 3 intentos: {e}")
            prompt = f"Tu JSON anterior era inválido. Error: {e}\nRespuesta anterior:\n{raw}\nCorregí y devolvé SOLO JSON válido."


# ── Chunking ──────────────────────────────────────────────────────────────────

def sub_chunkear(texto: str, encabezado: str) -> list[str]:
    if len(texto) <= MAX_CHARS_CHUNK:
        return [texto]
    lineas = texto.split("\n")
    chunks, actual = [], encabezado + "\n" if encabezado else ""
    for linea in lineas:
        if len(actual) + len(linea) > MAX_CHARS_CHUNK:
            if actual.strip():
                chunks.append(actual.strip())
            actual = (encabezado + "\n" if encabezado else "") + linea + "\n"
        else:
            actual += linea + "\n"
    if actual.strip():
        chunks.append(actual.strip())
    return chunks or [texto[:MAX_CHARS_CHUNK]]


# ── DB ────────────────────────────────────────────────────────────────────────

def hash_ya_existe(cur, content_hash: str) -> str | None:
    cur.execute("SELECT id FROM documentos WHERE content_hash = %s", (content_hash,))
    row = cur.fetchone()
    return str(row[0]) if row else None


def guardar_chunks(cur, documento_id: str, expediente_id: str | None,
                   chunks: list[dict], embeddings: list[list[float]],
                   tipo_doc: str, subtipo: str | None,
                   clasificacion: dict, resultado: dict,
                   content_hash: str) -> None:

    cur.execute("DELETE FROM doc_chunks WHERE documento_id = %s::uuid", (documento_id,))

    for chunk, emb in zip(chunks, embeddings):
        emb_str = "[" + ",".join(map(str, emb)) + "]"
        cur.execute(
            """INSERT INTO doc_chunks (
                documento_id, expediente_id,
                tipo_doc, subtipo, seccion,
                chunk_texto, chunk_index, sub_chunk_index,
                embedding_model, embedding
            ) VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::vector)""",
            (
                documento_id, expediente_id,
                tipo_doc, subtipo, chunk["seccion"],
                chunk["chunk_texto"], chunk["chunk_index"], chunk["sub_chunk_index"],
                EMBEDDING_MODEL, emb_str,
            ),
        )

    cur.execute(
        """UPDATE documentos SET
            indexado=TRUE, indexado_at=NOW(),
            tipo_doc=%s, subtipo=%s, estructura=%s,
            content_hash=%s,
            metadata=CAST(%s AS jsonb),
            raw_llm_response=CAST(%s AS jsonb),
            parser_version=%s
        WHERE id=%s::uuid""",
        (
            tipo_doc, subtipo, clasificacion.get("estructura", "corrido"),
            content_hash,
            json.dumps(resultado.get("metadata", {}), ensure_ascii=False),
            json.dumps(resultado, ensure_ascii=False),
            PARSER_VERSION, documento_id,
        ),
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def procesar_archivo(ruta: Path, modelo, client: anthropic.Anthropic,
                     conn, expediente_id: str | None = None) -> dict:
    cur = conn.cursor()

    # Extraer texto
    texto_raw = extraer_texto(ruta)
    valido, razon = validar_archivo(ruta, texto_raw)
    if not valido:
        return {"archivo": ruta.name, "ok": False, "error": razon}

    texto = normalizar(texto_raw)
    content_hash = hashlib.sha256(texto.encode()).hexdigest()

    # Deduplicación
    doc_existente = hash_ya_existe(cur, content_hash)
    if doc_existente:
        return {"archivo": ruta.name, "ok": False, "error": f"Duplicado — ya existe doc {doc_existente}"}

    # Clasificar + parsear
    clasificacion = clasificar(client, texto)
    tipo_doc = clasificacion.get("tipo_documento", "otro")
    subtipo  = clasificacion.get("subtipo")

    resultado = parsear(client, texto, tipo_doc)

    # Validar integridad
    texto_reconstruido = " ".join(s.get("texto", "") for s in resultado.get("secciones", []))
    cobertura = len(texto_reconstruido) / max(len(texto), 1)
    if cobertura < INTEGRIDAD_MIN:
        return {"archivo": ruta.name, "ok": False,
                "error": f"Cobertura insuficiente: {cobertura:.0%}"}

    # Sub-chunking
    chunks_para_embed = []
    for sec in resultado.get("secciones", []):
        tipo_sec  = sec.get("tipo", "otro")
        texto_sec = sec.get("texto", "").strip()
        orden     = sec.get("orden", 0)
        if not texto_sec:
            continue
        for sub_idx, sub_texto in enumerate(sub_chunkear(texto_sec, f"[{tipo_sec.upper()}]")):
            chunks_para_embed.append({
                "seccion": tipo_sec, "chunk_texto": sub_texto,
                "chunk_index": orden, "sub_chunk_index": sub_idx,
            })

    if not chunks_para_embed:
        return {"archivo": ruta.name, "ok": False, "error": "Sin chunks tras parseo"}

    # Embeddings
    textos_emb = [f"passage: {c['chunk_texto']}" for c in chunks_para_embed]
    vectores = modelo.encode(textos_emb, batch_size=16, normalize_embeddings=True,
                             show_progress_bar=False).tolist()

    # Buscar documento_id en DB (debe existir ya creado por Laravel, o crear uno temporal)
    cur.execute("SELECT id, expediente_id FROM documentos WHERE content_hash = %s", (content_hash,))
    row = cur.fetchone()

    if row:
        documento_id  = str(row[0])
        expediente_id = str(row[1]) if row[1] else expediente_id
    else:
        # Crear registro mínimo para docs que no vienen de Laravel
        cur.execute(
            """INSERT INTO documentos
                (nombre_archivo, formato, tipo_documento, estado_extraccion, content_hash,
                 texto_extraido, usuario_id)
               VALUES (%s, %s, %s, 'procesado', %s, %s::jsonb,
                   (SELECT id FROM users WHERE rol='administrador' LIMIT 1))
               RETURNING id""",
            (
                ruta.name,
                ruta.suffix.lstrip(".").lower(),
                tipo_doc,
                content_hash,
                json.dumps({"texto_completo": texto}, ensure_ascii=False),
            ),
        )
        documento_id = str(cur.fetchone()[0])

    guardar_chunks(cur, documento_id, expediente_id, chunks_para_embed, vectores,
                   tipo_doc, subtipo, clasificacion, resultado, content_hash)
    conn.commit()
    cur.close()

    return {
        "archivo": ruta.name, "ok": True,
        "tipo": tipo_doc, "subtipo": subtipo,
        "chunks": len(chunks_para_embed),
        "secciones": list({c["seccion"] for c in chunks_para_embed}),
        "confianza": clasificacion.get("confianza"),
    }


def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/indexar_documentos.py <carpeta_de_docs>")
        sys.exit(1)

    carpeta = Path(sys.argv[1])
    if not carpeta.is_dir():
        print(f"Error: '{carpeta}' no es un directorio")
        sys.exit(1)

    archivos = [f for f in carpeta.iterdir() if f.suffix.lower() in EXTENSIONES_VALIDAS]
    if not archivos:
        print(f"No se encontraron .pdf ni .docx en {carpeta}")
        sys.exit(1)

    print(f"\n=== INDEXADOR MASIVO DE DOCUMENTOS ===")
    print(f"Carpeta: {carpeta}")
    print(f"Archivos encontrados: {len(archivos)}\n")

    print("Cargando modelo de embeddings...")
    cache = os.getenv("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
    modelo = SentenceTransformer(MODEL_NAME, cache_folder=cache)
    print("Modelo listo\n")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    conn   = psycopg2.connect(DATABASE_URL)

    resultados = {"ok": [], "error": []}

    for i, ruta in enumerate(sorted(archivos), 1):
        print(f"[{i}/{len(archivos)}] {ruta.name}")
        try:
            res = procesar_archivo(ruta, modelo, client, conn)
        except Exception as e:
            res = {"archivo": ruta.name, "ok": False, "error": str(e)}

        if res["ok"]:
            print(f"  ✓ {res['tipo']} ({res.get('subtipo','')}) | "
                  f"{res['chunks']} chunks | confianza: {res.get('confianza','?')}")
            resultados["ok"].append(res)
        else:
            print(f"  ✗ {res['error']}")
            resultados["error"].append(res)

    conn.close()

    print(f"\n=== RESUMEN ===")
    print(f"  Exitosos : {len(resultados['ok'])}")
    print(f"  Errores  : {len(resultados['error'])}")

    if resultados["error"]:
        print("\nArchivos con error:")
        for r in resultados["error"]:
            print(f"  - {r['archivo']}: {r['error']}")


if __name__ == "__main__":
    main()
