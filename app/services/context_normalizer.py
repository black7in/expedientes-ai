from dataclasses import dataclass, asdict
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text


@dataclass
class ContextoJuridico:
    """
    Estructura unificada de contexto para la generación de documentos legales.
    Se construye desde un expediente del sistema (modo A) o desde un
    formulario manual ingresado por el abogado (modo B).
    """

    tipo_documento: str          # "demanda", "memorial", "apelacion", "nulidad", "contrato"
    demandante: str
    demandado: str
    juzgado: str
    ciudad: str
    tipo_proceso: str
    hechos: str
    etapa_procesal: Optional[str] = None
    actuaciones: Optional[list] = None
    instrucciones_extra: Optional[str] = None
    materia: str = "civil"

    def to_dict(self) -> dict:
        return asdict(self)


def normalizar_desde_expediente(
    expediente_id: str,
    tipo_documento: str,
    instrucciones: Optional[str],
    db: Session,
) -> ContextoJuridico:
    """
    Construye el contexto desde un expediente almacenado en el sistema.
    Lee partes, juzgado, tipo de proceso y actuaciones recientes.
    """
    exp = db.execute(
        text("""
            SELECT e.estado,
                   j.nombre  AS juzgado,
                   j.ciudad,
                   tp.nombre AS tipo_proceso
            FROM expedientes e
            LEFT JOIN juzgados j      ON e.juzgado_id      = j.id
            LEFT JOIN tipos_proceso tp ON e.tipo_proceso_id = tp.id
            WHERE e.id = CAST(:id AS uuid)
        """),
        {"id": expediente_id},
    ).fetchone()

    if not exp:
        raise ValueError(f"Expediente {expediente_id} no encontrado")

    partes = db.execute(
        text("""
            SELECT p.nombre, p.apellido, pa.rol_procesal
            FROM partes pa
            JOIN personas p ON pa.persona_id = p.id
            WHERE pa.expediente_id = CAST(:id AS uuid)
        """),
        {"id": expediente_id},
    ).fetchall()

    demandante = next(
        (f"{p[0]} {p[1]}" for p in partes if p[2] == "demandante"), ""
    )
    demandado = next(
        (f"{p[0]} {p[1]}" for p in partes if p[2] == "demandado"), ""
    )

    actuaciones_rows = db.execute(
        text("""
            SELECT descripcion, fecha, tipo
            FROM actuaciones
            WHERE expediente_id = CAST(:id AS uuid)
            ORDER BY fecha DESC
            LIMIT 5
        """),
        {"id": expediente_id},
    ).fetchall()

    actuaciones = [
        {"descripcion": a[0], "fecha": str(a[1]), "tipo": a[2]}
        for a in actuaciones_rows
    ]

    return ContextoJuridico(
        tipo_documento=tipo_documento,
        demandante=demandante,
        demandado=demandado,
        juzgado=exp[1] or "",
        ciudad=exp[2] or "Santa Cruz de la Sierra",
        tipo_proceso=exp[3] or "",
        hechos="",  # el abogado puede agregar instrucciones en instrucciones_extra
        etapa_procesal=exp[0],
        actuaciones=actuaciones,
        instrucciones_extra=instrucciones,
    )


def normalizar_desde_formulario(datos: dict) -> ContextoJuridico:
    """
    Construye el contexto desde datos ingresados manualmente en el formulario.
    No requiere ningún expediente en el sistema.
    """
    return ContextoJuridico(
        tipo_documento=datos.get("tipo_documento", "demanda"),
        demandante=datos.get("demandante", ""),
        demandado=datos.get("demandado", ""),
        juzgado=datos.get("juzgado", ""),
        ciudad=datos.get("ciudad", "Santa Cruz de la Sierra"),
        tipo_proceso=datos.get("tipo_proceso", ""),
        hechos=datos.get("hechos", ""),
        etapa_procesal=None,
        actuaciones=None,
        instrucciones_extra=datos.get("instrucciones_extra"),
    )
