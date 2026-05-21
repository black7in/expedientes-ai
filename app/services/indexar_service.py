import asyncio
import fitz  # PyMuPDF
import hashlib
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from .embedding_service import EmbeddingService
from .chunking_service import chunk_documento, chunk_articulo_legal
from .normalizer import normalizar
from .classifier import clasificar_documento
from .parser import parsear_documento
from .validator import validar_parse, sub_chunkear

_EMBEDDING_MODEL = "multilingual-e5-large"
_PARSER_VERSION  = "parser-v1.0"


async def indexar_documento(
    documento_id: str,
    texto: str,
    expediente_id: str | None,
    tipo_doc: str | None,
    db: AsyncSession,
) -> dict:
    try:
        # 1. Normalizar
        texto_norm = normalizar(texto)

        # 2. Content hash para deduplicación
        content_hash = hashlib.sha256(texto_norm.encode()).hexdigest()

        # 3. Clasificar (LLM — async)
        clasificacion = await clasificar_documento(texto_norm)
        tipo_doc      = clasificacion.get("tipo_documento", tipo_doc or "otro")
        subtipo       = clasificacion.get("subtipo")
        estructura    = clasificacion.get("estructura", "corrido")

        # 4. Parsear con LLM (async)
        resultado = await parsear_documento(texto_norm, tipo_doc)
        raw_llm   = resultado

        # 5. Validar
        validar_parse(resultado, texto_norm, tipo_doc)

        metadata_doc = resultado.get("metadata", {})
        secciones    = resultado.get("secciones", [])

        # 6. Construir chunks con sub-chunking
        chunks_para_embed: list[dict] = []
        for sec in secciones:
            tipo_sec  = sec.get("tipo", "otro")
            texto_sec = sec.get("texto", "").strip()
            orden     = sec.get("orden", 0)
            if not texto_sec:
                continue
            for sub_idx, sub_texto in enumerate(sub_chunkear(texto_sec, f"[{tipo_sec.upper()}]")):
                chunks_para_embed.append({
                    "seccion":         tipo_sec,
                    "chunk_texto":     sub_texto,
                    "chunk_index":     orden,
                    "sub_chunk_index": sub_idx,
                })

        if not chunks_para_embed:
            raise ValueError("El parser no generó ninguna sección con contenido.")

        # 7. Embeddings (CPU-bound — hilo separado)
        textos_emb = [c["chunk_texto"] for c in chunks_para_embed]
        embeddings = await asyncio.to_thread(EmbeddingService.encode_passages, textos_emb)

        # 8. Persistencia transaccional
        await db.execute(
            text("DELETE FROM doc_chunks WHERE documento_id = CAST(:id AS uuid)"),
            {"id": documento_id},
        )

        for chunk, embedding in zip(chunks_para_embed, embeddings):
            emb_str = EmbeddingService.to_pgvector_str(embedding)
            await db.execute(
                text("""
                    INSERT INTO doc_chunks (
                        documento_id, expediente_id,
                        tipo_doc, subtipo, seccion,
                        chunk_texto, chunk_index, sub_chunk_index,
                        embedding_model, embedding
                    ) VALUES (
                        CAST(:doc_id AS uuid),
                        CAST(:exp_id AS uuid),
                        :tipo_doc, :subtipo, :seccion,
                        :chunk_texto, :chunk_index, :sub_chunk_index,
                        :embedding_model, CAST(:embedding AS vector)
                    )
                """),
                {
                    "doc_id":          documento_id,
                    "exp_id":          expediente_id,
                    "tipo_doc":        tipo_doc,
                    "subtipo":         subtipo,
                    "seccion":         chunk["seccion"],
                    "chunk_texto":     chunk["chunk_texto"],
                    "chunk_index":     chunk["chunk_index"],
                    "sub_chunk_index": chunk["sub_chunk_index"],
                    "embedding_model": _EMBEDDING_MODEL,
                    "embedding":       emb_str,
                },
            )

        await db.execute(
            text("""
                UPDATE documentos SET
                    indexado         = TRUE,
                    indexado_at      = NOW(),
                    tipo_doc         = :tipo_doc,
                    subtipo          = :subtipo,
                    estructura       = :estructura,
                    content_hash     = :content_hash,
                    metadata         = CAST(:metadata AS jsonb),
                    raw_llm_response = CAST(:raw_llm AS jsonb),
                    parser_version   = :parser_version
                WHERE id = CAST(:id AS uuid)
            """),
            {
                "tipo_doc":       tipo_doc,
                "subtipo":        subtipo,
                "estructura":     estructura,
                "content_hash":   content_hash,
                "metadata":       json.dumps(metadata_doc, ensure_ascii=False),
                "raw_llm":        json.dumps(raw_llm, ensure_ascii=False),
                "parser_version": _PARSER_VERSION,
                "id":             documento_id,
            },
        )

        await db.commit()

        return {
            "chunks":         len(chunks_para_embed),
            "tipo_documento": tipo_doc,
            "subtipo":        subtipo,
            "estructura":     estructura,
            "secciones":      list({c["seccion"] for c in chunks_para_embed}),
        }

    except Exception as e:
        await db.rollback()
        await db.execute(
            text("""
                UPDATE documentos SET
                    estado_extraccion = 'error',
                    error_mensaje     = :msg
                WHERE id = CAST(:id AS uuid)
            """),
            {"msg": str(e), "id": documento_id},
        )
        await db.commit()
        raise


async def indexar_ley_desde_pdf(
    ruta_pdf: str,
    nombre_ley: str,
    materia: str,
    db: AsyncSession,
) -> int:
    def _leer_pdf() -> str:
        doc = fitz.open(ruta_pdf)
        texto = "".join(pagina.get_text() for pagina in doc)
        doc.close()
        return texto

    texto_completo = await asyncio.to_thread(_leer_pdf)
    articulos = chunk_articulo_legal(texto_completo)

    if not articulos:
        raise ValueError(f"No se encontraron artículos en {nombre_ley}. Verificar formato del PDF.")

    textos = [a["chunk_texto"] for a in articulos]
    embeddings = await asyncio.to_thread(EmbeddingService.encode_passages, textos)

    await db.execute(
        text("DELETE FROM leyes_chunks WHERE ley = :ley"),
        {"ley": nombre_ley},
    )

    for articulo, embedding in zip(articulos, embeddings):
        emb_str = EmbeddingService.to_pgvector_str(embedding)
        await db.execute(
            text("""
                INSERT INTO leyes_chunks
                    (ley, numero_articulo, materia, chunk_texto, embedding)
                VALUES (:ley, :numero_articulo, :materia, :chunk_texto, CAST(:embedding AS vector))
            """),
            {
                "ley":             nombre_ley,
                "numero_articulo": articulo["numero_articulo"],
                "materia":         materia,
                "chunk_texto":     articulo["chunk_texto"],
                "embedding":       emb_str,
            },
        )

    await db.commit()
    return len(articulos)


async def indexar_auto_supremo(
    numero_auto: str,
    fecha: str | None,
    materia: str,
    sala: str | None,
    texto: str,
    db: AsyncSession,
) -> int:
    chunks = chunk_documento(texto, chunk_size=400, overlap=80)
    if not chunks:
        return 0

    embeddings = await asyncio.to_thread(EmbeddingService.encode_passages, chunks)

    await db.execute(
        text("DELETE FROM jurisprudencia_chunks WHERE numero_auto = :auto"),
        {"auto": numero_auto},
    )

    for chunk, embedding in zip(chunks, embeddings):
        emb_str = EmbeddingService.to_pgvector_str(embedding)
        await db.execute(
            text("""
                INSERT INTO jurisprudencia_chunks
                    (numero_auto, fecha_resolucion, materia, sala, chunk_texto, embedding)
                VALUES (
                    :numero_auto,
                    CAST(:fecha AS date),
                    :materia,
                    :sala,
                    :chunk_texto,
                    CAST(:embedding AS vector)
                )
            """),
            {
                "numero_auto": numero_auto,
                "fecha":       fecha,
                "materia":     materia,
                "sala":        sala,
                "chunk_texto": chunk,
                "embedding":   emb_str,
            },
        )

    await db.commit()
    return len(chunks)
