import re
import unicodedata


def normalizar(texto: str) -> str:
    # Normalización Unicode NFC (tildes compuestas)
    texto = unicodedata.normalize("NFC", texto)

    # Guiones de corte de palabra al final de línea
    texto = re.sub(r"-\n", "", texto)

    # Números de página sueltos (línea con solo un número)
    texto = re.sub(r"\n\s*\d+\s*\n", "\n", texto)

    # Espacios múltiples
    texto = re.sub(r" {2,}", " ", texto)

    # Más de 2 saltos de línea consecutivos
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    # Líneas que son solo espacios
    texto = re.sub(r"\n[ \t]+\n", "\n\n", texto)

    return texto.strip()
