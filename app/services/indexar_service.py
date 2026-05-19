import fitz  # PyMuPDF
from sqlalchemy.orm import Session
from sqlalchemy import text

from .embedding_service import EmbeddingService
from .chunking_service import chunk_documento, chunk_articulo_legal


def indexar_documento(
    documento_id: str,
    texto: str,
    expediente_id: str | None,
    tipo_doc: str,
    db: Session,
) -> int:
    """
    Indexa un documento del estudio en doc_chunks.
    Se llama automáticamente desde Laravel cuando el abogado confirma un documento.
    Retorna la cantidad de chunks generados.
    """
    # Eliminar indexación previa si existe (re-indexado)
    db.execute(
        text("DELETE FROM doc_chunks WHERE documento_id = CAST(:id AS uuid)"),
        {"id": documento_id},
    )

    chunks = chunk_documento(texto)
    if not chunks:
        return 0

    embeddings = EmbeddingService.encode_passages(chunks)

    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        emb_str = EmbeddingService.to_pgvector_str(embedding)
        db.execute(
            text("""
                INSERT INTO doc_chunks
                    (documento_id, expediente_id, tipo_doc, chunk_texto, chunk_index, embedding)
                VALUES (
                    CAST(:doc_id AS uuid),
                    CAST(:exp_id AS uuid),
                    :tipo_doc,
                    :chunk_texto,
                    :chunk_index,
                    CAST(:embedding AS vector)
                )
            """),
            {
                "doc_id":      documento_id,
                "exp_id":      expediente_id,
                "tipo_doc":    tipo_doc,
                "chunk_texto": chunk,
                "chunk_index": i,
                "embedding":   emb_str,
            },
        )

    # Marcar documento como indexado
    db.execute(
        text("""
            UPDATE documentos
            SET indexado = TRUE, indexado_at = NOW(), tipo_doc = :tipo_doc
            WHERE id = CAST(:id AS uuid)
        """),
        {"id": documento_id, "tipo_doc": tipo_doc},
    )

    db.commit()
    return len(chunks)


def indexar_ley_desde_pdf(
    ruta_pdf: str,
    nombre_ley: str,
    materia: str,
    db: Session,
) -> int:
    """
    Indexa un PDF de ley boliviana artículo por artículo en leyes_chunks.
    Ejecutar una vez por cada ley (o al actualizar).
    Retorna la cantidad de artículos indexados.
    """
    doc = fitz.open(ruta_pdf)
    texto_completo = ""
    for pagina in doc:
        texto_completo += pagina.get_text()
    doc.close()

    articulos = chunk_articulo_legal(texto_completo)
    if not articulos:
        raise ValueError(f"No se encontraron artículos en {nombre_ley}. Verificar formato del PDF.")

    textos = [a["chunk_texto"] for a in articulos]
    embeddings = EmbeddingService.encode_passages(textos)

    # Limpiar indexación previa de esta ley
    db.execute(
        text("DELETE FROM leyes_chunks WHERE ley = :ley"),
        {"ley": nombre_ley},
    )

    for articulo, embedding in zip(articulos, embeddings):
        emb_str = EmbeddingService.to_pgvector_str(embedding)
        db.execute(
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

    db.commit()
    return len(articulos)


def indexar_auto_supremo(
    numero_auto: str,
    fecha: str | None,
    materia: str,
    sala: str | None,
    texto: str,
    db: Session,
) -> int:
    """
    Indexa un Auto Supremo del TSJ Bolivia en jurisprudencia_chunks.
    Los Autos son largos — se dividen en chunks de ~400 tokens.
    Retorna la cantidad de chunks generados.
    """
    chunks = chunk_documento(texto, chunk_size=400, overlap=80)
    if not chunks:
        return 0

    embeddings = EmbeddingService.encode_passages(chunks)

    # Eliminar si ya existe
    db.execute(
        text("DELETE FROM jurisprudencia_chunks WHERE numero_auto = :auto"),
        {"auto": numero_auto},
    )

    for chunk, embedding in zip(chunks, embeddings):
        emb_str = EmbeddingService.to_pgvector_str(embedding)
        db.execute(
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

    db.commit()
    return len(chunks)
