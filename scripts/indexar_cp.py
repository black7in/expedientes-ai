"""
indexar_cp.py — Carga el Código Penal boliviano en leyes_chunks (pgvector).

Uso (dentro del contenedor ai):
    python scripts/indexar_cp.py /ruta/al/codigo_penal.pdf

O desde fuera:
    docker compose exec ai python scripts/indexar_cp.py /app/recursos/leyes/codigo_penal.pdf
"""

import os
import re
import sys
import json
import psycopg2
import pdfplumber
from sentence_transformers import SentenceTransformer

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://laravel:secret@postgres:5432/expedientes_juridicos")
MODEL_NAME   = "intfloat/multilingual-e5-large"
NOMBRE_LEY   = "Código Penal"
MATERIA      = "penal"
CHUNK_MAX    = 800

PATRONES = {
    "libro":    re.compile(r"^LIBRO\s+\w+", re.IGNORECASE),
    "titulo":   re.compile(r"^T[ÍI]TULO\s+[IVX\d]+", re.IGNORECASE),
    "capitulo": re.compile(r"^CAP[ÍI]TULO\s+\w+", re.IGNORECASE),
    "seccion":  re.compile(r"^SECCI[OÓ]N\s+[IVX\d]+", re.IGNORECASE),
    "articulo": re.compile(
        r"^ART[ÍI]CULO\s+\d+\."
        r"|^Art\.\s+\d+[o0]?[\.\-]{0,3}",
        re.IGNORECASE,
    ),
}


# ── Extracción ────────────────────────────────────────────────────────────────

def extraer_texto(ruta: str) -> str:
    texto = ""
    with pdfplumber.open(ruta) as pdf:
        print(f"   Páginas: {len(pdf.pages)}")
        for pagina in pdf.pages:
            contenido = pagina.extract_text()
            if contenido:
                texto += contenido + "\n"
    return texto


def limpiar_texto(texto: str) -> str:
    texto = re.sub(r"-\n", "", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    texto = re.sub(r" {2,}", " ", texto)
    return texto.strip()


def unir_lineas_partidas(texto: str) -> str:
    lineas = texto.split("\n")
    resultado = []
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            resultado.append("")
            continue
        if (resultado and resultado[-1] and
                resultado[-1].isupper() and linea.isupper() and
                not any(p.match(linea) for p in PATRONES.values())):
            resultado[-1] += " " + linea
        else:
            resultado.append(linea)
    return "\n".join(resultado)


# ── Chunking ──────────────────────────────────────────────────────────────────

def _detectar_tipo(linea: str) -> str | None:
    for tipo, patron in PATRONES.items():
        if patron.match(linea.strip()):
            return tipo
    return None


def _chunkear_largo(encabezado: str, texto: str) -> list[str]:
    lineas = texto.split("\n")
    chunks, actual = [], encabezado + "\n"
    for linea in lineas:
        if len(actual) + len(linea) > CHUNK_MAX:
            if actual.strip():
                chunks.append(actual.strip())
            actual = encabezado + "\n" + linea + "\n"
        else:
            actual += linea + "\n"
    if actual.strip():
        chunks.append(actual.strip())
    return chunks


def _crear_chunks(encabezado, texto, numero, nombre, estado, idx_inicio):
    texto_completo = encabezado + "\n" + texto
    partes = [texto_completo] if len(texto_completo) <= CHUNK_MAX else _chunkear_largo(encabezado, texto)
    return [
        {
            "texto":        p.strip(),
            "articulo":     numero,
            "nombre":       nombre,
            "libro":        estado.get("libro"),
            "titulo":       estado.get("titulo"),
            "capitulo":     estado.get("capitulo"),
            "seccion":      estado.get("seccion"),
            "parte":        i + 1,
            "total_partes": len(partes),
        }
        for i, p in enumerate(partes)
    ]


def parsear(texto: str) -> list[dict]:
    estado = {"libro": None, "titulo": None, "capitulo": None, "seccion": None}
    chunks = []
    articulo_actual = texto_articulo = numero_articulo = None
    nombre_articulo = ""

    def flush():
        nonlocal texto_articulo, articulo_actual
        if articulo_actual:
            chunks.extend(_crear_chunks(articulo_actual, texto_articulo, numero_articulo, nombre_articulo, estado, len(chunks)))
            texto_articulo = ""
            articulo_actual = None

    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea:
            if articulo_actual:
                texto_articulo += "\n"
            continue
        tipo = _detectar_tipo(linea)
        if tipo == "libro":
            flush(); estado.update({"libro": linea, "titulo": None, "capitulo": None, "seccion": None})
        elif tipo == "titulo":
            flush(); estado.update({"titulo": linea, "capitulo": None, "seccion": None})
        elif tipo == "capitulo":
            flush(); estado.update({"capitulo": linea, "seccion": None})
        elif tipo == "seccion":
            flush(); estado["seccion"] = linea
        elif tipo == "articulo":
            flush()
            articulo_actual = linea
            texto_articulo = ""
            m = re.search(r"\d+", linea); numero_articulo = m.group() if m else "?"
            m = re.search(r"\(([^)]+)\)", linea); nombre_articulo = m.group(1) if m else ""
        else:
            if articulo_actual:
                texto_articulo += linea + "\n"

    flush()
    return chunks


# ── Embeddings ────────────────────────────────────────────────────────────────

def generar_embeddings(chunks: list[dict], modelo) -> list[list[float]]:
    print(f"   Generando embeddings para {len(chunks)} chunks...")
    textos = [f"passage: {c['texto']}" for c in chunks]
    vectores = modelo.encode(textos, batch_size=16, show_progress_bar=True, normalize_embeddings=True)
    return vectores.tolist()


# ── Guardar en pgvector ───────────────────────────────────────────────────────

def guardar_en_db(chunks: list[dict], embeddings: list[list[float]]):
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    cur.execute("DELETE FROM leyes_chunks WHERE ley = %s", (NOMBRE_LEY,))
    print(f"   Registros anteriores eliminados")

    for chunk, emb in zip(chunks, embeddings):
        emb_str  = "[" + ",".join(map(str, emb)) + "]"
        metadata = {
            "libro": chunk["libro"], "titulo": chunk["titulo"],
            "capitulo": chunk["capitulo"], "seccion": chunk["seccion"],
            "parte": chunk["parte"], "total_partes": chunk["total_partes"],
        }
        cur.execute(
            """
            INSERT INTO leyes_chunks
                (ley, numero_articulo, titulo_articulo, materia, chunk_texto, metadata, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
            """,
            (
                NOMBRE_LEY,
                chunk["articulo"],
                chunk["nombre"] or None,
                MATERIA,
                chunk["texto"],
                json.dumps(metadata, ensure_ascii=False),
                emb_str,
            ),
        )

    conn.commit()
    cur.close()
    conn.close()
    print(f"   {len(chunks)} chunks guardados en leyes_chunks")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/indexar_cp.py <ruta_pdf>")
        sys.exit(1)

    ruta_pdf = sys.argv[1]
    print(f"\n=== INDEXADOR — {NOMBRE_LEY} ===")
    print(f"PDF: {ruta_pdf}\n")

    print("1. Extrayendo texto...")
    texto = extraer_texto(ruta_pdf)
    texto = limpiar_texto(texto)
    texto = unir_lineas_partidas(texto)
    print(f"   {len(texto)} caracteres")

    print("2. Parseando artículos...")
    chunks = parsear(texto)
    print(f"   {len(chunks)} chunks generados")
    articulos_unicos = len(set(c["articulo"] for c in chunks))
    divididos = len(set(c["articulo"] for c in chunks if c["total_partes"] > 1))
    print(f"   Artículos únicos: {articulos_unicos}  |  Divididos: {divididos}")

    print("3. Cargando modelo de embeddings...")
    cache = os.getenv("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
    modelo = SentenceTransformer(MODEL_NAME, cache_folder=cache)

    print("4. Generando embeddings...")
    embeddings = generar_embeddings(chunks, modelo)

    print("5. Guardando en PostgreSQL...")
    guardar_en_db(chunks, embeddings)

    print(f"\n✓ Indexación completa — {NOMBRE_LEY}")


if __name__ == "__main__":
    main()
