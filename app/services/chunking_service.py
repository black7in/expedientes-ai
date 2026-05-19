import re


def chunk_documento(texto: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """
    Divide texto en chunks con overlap para documentos del estudio jurídico.
    Usa aprox. 4 chars/token para español.

    chunk_size=500  → ~500 tokens por chunk
    overlap=100     → 100 tokens de solapamiento entre chunks consecutivos
    """
    char_size = chunk_size * 4
    char_overlap = overlap * 4

    chunks: list[str] = []
    start = 0

    while start < len(texto):
        end = start + char_size
        chunk = texto[start:end]

        # Cortar en punto de quiebre natural para no romper oraciones
        if end < len(texto):
            last_break = max(
                chunk.rfind(".\n"),
                chunk.rfind("\n\n"),
                chunk.rfind(". "),
            )
            if last_break > char_size * 0.5:
                chunk = chunk[: last_break + 1]
                end = start + last_break + 1

        chunk = chunk.strip()
        if len(chunk) > 50:
            chunks.append(chunk)

        start = end - char_overlap

    return chunks


def chunk_articulo_legal(texto_ley: str) -> list[dict]:
    """
    Divide el texto de una ley boliviana en chunks por artículo.
    Cada artículo queda como un chunk independiente con su número como metadata.

    Detecta patrones de la legislación boliviana:
      "Artículo 110.-"  "ARTÍCULO 110."  "Art. 110.-"
    """
    patron = re.compile(
        r"(Art[íi]culo\s+\d+[°º]?[\.\-]|ARTÍCULO\s+\d+[\.\-]|Art\.\s+\d+[\.\-])",
        re.IGNORECASE,
    )

    partes = patron.split(texto_ley)
    articulos: list[dict] = []

    i = 1
    while i < len(partes) - 1:
        encabezado = partes[i].strip()
        contenido = partes[i + 1].strip() if i + 1 < len(partes) else ""

        numero_match = re.search(r"\d+", encabezado)
        if numero_match and contenido:
            texto_completo = f"{encabezado} {contenido[:2000]}"
            articulos.append(
                {
                    "numero_articulo": numero_match.group(),
                    "chunk_texto": texto_completo,
                }
            )
        i += 2

    return articulos
