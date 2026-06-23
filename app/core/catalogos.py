TIPOS_MEMORIAL = [
    "querella", "demanda", "contestacion", "recurso_apelacion",
    "recurso_casacion", "memorial_respuesta", "memorial_alegato",
    "solicitud", "denuncia", "otro",
]

TIPOS_ACCION = [
    # Penal
    "estafa", "robo", "hurto", "lesiones", "homicidio", "violencia_familiar",
    # Civil / Comercial
    "ejecucion_hipotecaria", "cobro_ejecutivo", "resolucion_contrato",
    "cumplimiento_contrato", "nulidad_contrato", "daños_perjuicios",
    "reivindicatoria", "usucapion", "desalojo", "interdicto",
    "nulidad_escritura", "mejor_derecho_propiedad",
    # Familiar
    "divorcio", "asistencia_familiar", "filiacion", "guarda_custodia",
    # Laboral
    "despido_injustificado", "beneficios_sociales", "reincorporacion",
    # Constitucional
    "amparo_constitucional", "accion_libertad", "accion_popular",
    "accion_cumplimiento",
    "otro",
]

MATERIAS = [
    "penal", "civil", "laboral", "familiar",
    "constitucional", "administrativo", "comercial", "tributario",
]

INSTANCIAS = ["primera", "apelacion", "casacion"]

FORMATOS = ["estructurado", "corrido", "mixto"]

ESTILOS = ["formal_clasico", "moderno_directo", "tecnico_extenso", "conciso"]

ORIGENES = ["caso_real", "plantilla_experta", "usuario_subido", "generado_validado"]

CALIDADES = ["premium", "estandar", "baja"]
