from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/rag-stats")
async def rag_stats(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("""
        SELECT
            (SELECT COUNT(*)            FROM leyes_chunks)                  AS leyes_chunks,
            (SELECT COUNT(DISTINCT ley) FROM leyes_chunks)                  AS leyes_count,
            (SELECT COUNT(*)            FROM plantillas_memoriales)             AS moldes_total,
            (SELECT COUNT(*) FROM plantillas_memoriales WHERE activo = true)    AS moldes_activos
    """))
    row = result.fetchone()

    leyes_result = await db.execute(text("""
        SELECT ley, materia, COUNT(*) AS chunks
        FROM leyes_chunks
        GROUP BY ley, materia
        ORDER BY ley
    """))
    leyes = leyes_result.fetchall()

    return {
        "stats": {
            "leyes_chunks":   row[0],
            "leyes_count":    row[1],
            "moldes_total":   row[2],
            "moldes_activos": row[3],
        },
        "leyes": [
            {"ley": r[0], "materia": r[1], "chunks": r[2]}
            for r in leyes
        ],
    }
