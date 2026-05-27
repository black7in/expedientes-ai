import re
from bs4 import BeautifulSoup

ELEMENTOS_PERMITIDOS = {"h1", "h2", "p", "strong", "em", "ul", "ol", "li"}


def limpiar_html(html: str) -> str:
    """
    Normaliza HTML a solo los elementos permitidos.
    Elimina atributos inline, divs, spans y cualquier tag no permitido
    manteniendo el contenido de texto.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Eliminar scripts, styles y comentarios
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    # Quitar todos los atributos de cada tag
    for tag in soup.find_all(True):
        tag.attrs = {}

    # Convertir tags no permitidos a su contenido (unwrap)
    for tag in soup.find_all(True):
        if tag.name not in ELEMENTOS_PERMITIDOS:
            tag.unwrap()

    # Colapsar whitespace excesivo
    resultado = str(soup)
    resultado = re.sub(r"[ \t]+", " ", resultado)
    resultado = re.sub(r"\n{3,}", "\n\n", resultado)
    return resultado.strip()


def validar_html(html: str) -> bool:
    """Verifica que el HTML solo contenga elementos permitidos."""
    soup = BeautifulSoup(html, "html.parser")
    return all(tag.name in ELEMENTOS_PERMITIDOS for tag in soup.find_all(True))


def texto_para_hash(html: str) -> str:
    """Extrae texto plano del HTML, normalizado para deduplicación."""
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text(separator=" ")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip().lower()
