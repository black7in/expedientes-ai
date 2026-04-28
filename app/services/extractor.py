import os
import fitz  # PyMuPDF
from docx import Document


def extraer_pdf(ruta: str) -> dict:
    """Extrae texto de un archivo PDF página por página."""
    doc = fitz.open(ruta)
    paginas_texto = []

    for num, pagina in enumerate(doc, start=1):
        texto = pagina.get_text("text")
        paginas_texto.append({"pagina": num, "texto": texto.strip()})

    doc.close()

    texto_completo = "\n\n".join(
        p["texto"] for p in paginas_texto if p["texto"]
    )

    metadata = {}
    try:
        meta = fitz.open(ruta).metadata
        metadata = {k: v for k, v in meta.items() if v}
    except Exception:
        pass

    return {
        "texto_completo": texto_completo,
        "paginas": len(paginas_texto),
        "paginas_detalle": paginas_texto,
        "metadata": metadata,
    }


def extraer_docx(ruta: str) -> dict:
    """Extrae texto de un archivo DOCX."""
    doc = Document(ruta)
    parrafos = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    texto_completo = "\n\n".join(parrafos)

    return {
        "texto_completo": texto_completo,
        "paginas": 1,
        "parrafos": len(parrafos),
        "metadata": {},
    }


def extraer_texto(ruta: str, formato: str) -> dict:
    """Dispatcher: extrae texto según el formato del archivo."""
    if not os.path.exists(ruta):
        raise FileNotFoundError(f"Archivo no encontrado: {ruta}")

    if formato == "pdf":
        return extraer_pdf(ruta)
    elif formato == "docx":
        return extraer_docx(ruta)
    else:
        raise ValueError(f"Formato no soportado: {formato}")
