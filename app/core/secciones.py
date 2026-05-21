SECCIONES_VALIDAS: dict[str, list[str]] = {
    "demanda": [
        "encabezado", "tipo_demanda", "generales", "objeto",
        "hechos", "fundamento_derecho", "jurisprudencia",
        "doctrina", "petitorio", "otrosi", "cierre", "otro",
    ],
    "sentencia": [
        "encabezado", "vistos", "considerandos",
        "parte_resolutiva", "cierre", "otro",
    ],
    "contrato": [
        "encabezado", "partes", "antecedentes", "clausulas",
        "firmas", "otro",
    ],
    "memorial": [
        "encabezado", "objeto", "fundamento", "petitorio",
        "otrosi", "cierre", "otro",
    ],
    "notificacion": [
        "encabezado", "contenido", "cierre", "otro",
    ],
    "otro": ["contenido", "otro"],
}
