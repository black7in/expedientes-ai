import uuid
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Integer,
    String, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ─── Tabla existente (Sprint 1) ───────────────────────────────────────────────

class Documento(Base):
    __tablename__ = "documentos"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expediente_id    = Column(UUID(as_uuid=True), nullable=True)
    nombre_archivo   = Column(String(255), nullable=False)
    formato          = Column(String(10), nullable=False)
    tipo_documento   = Column(String(50), nullable=False)
    estado_extraccion= Column(String(20), nullable=False, default="pendiente")
    texto_extraido   = Column(JSONB, nullable=True)
    # Campos RAG (Sprint 2)
    tipo_doc         = Column(String(50), nullable=True)
    indexado         = Column(Boolean, default=False)
    indexado_at      = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, nullable=True)
    updated_at       = Column(DateTime, nullable=True)


# ─── Tablas RAG — chunks vectoriales ─────────────────────────────────────────

class LeyChunk(Base):
    __tablename__ = "leyes_chunks"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    ley             = Column(String(100), nullable=False)
    numero_articulo = Column(String(20), nullable=False)
    titulo_articulo = Column(Text, nullable=True)
    materia         = Column(String(50), nullable=False)
    chunk_texto     = Column(Text, nullable=False)
    meta            = Column(JSONB, default={})
    created_at      = Column(DateTime)
    # embedding: vector(1024) — solo se gestiona vía SQL raw


class JurisprudenciaChunk(Base):
    __tablename__ = "jurisprudencia_chunks"

    id               = Column(BigInteger, primary_key=True, autoincrement=True)
    numero_auto      = Column(String(50), nullable=False)
    fecha_resolucion = Column(Date, nullable=True)
    materia          = Column(String(50), nullable=False)
    sala             = Column(String(100), nullable=True)
    chunk_texto      = Column(Text, nullable=False)
    meta             = Column(JSONB, default={})
    created_at       = Column(DateTime)
    # embedding: vector(1024) — solo se gestiona vía SQL raw


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    documento_id  = Column(UUID(as_uuid=True), ForeignKey("documentos.id", ondelete="CASCADE"), nullable=True)
    expediente_id = Column(UUID(as_uuid=True), nullable=True)
    tipo_doc      = Column(String(50), nullable=True)
    chunk_texto   = Column(Text, nullable=False)
    chunk_index   = Column(Integer, nullable=False)
    meta          = Column(JSONB, default={})
    created_at    = Column(DateTime)
    # embedding: vector(1024) — solo se gestiona vía SQL raw


# ─── Tablas RAG — generaciones y borradores ──────────────────────────────────

class Generacion(Base):
    __tablename__ = "generaciones"

    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expediente_id    = Column(UUID(as_uuid=True), nullable=True)
    creado_por       = Column(UUID(as_uuid=True), nullable=False)
    tipo_documento   = Column(String(50), nullable=False)
    contexto_usado   = Column(JSONB, nullable=False)
    chunks_usados    = Column(JSONB, default=[])
    prompt_enviado   = Column(Text, nullable=True)
    borrador_generado= Column(Text, nullable=False)
    modelo_usado     = Column(String(50), nullable=True)
    tokens_usados    = Column(Integer, nullable=True)
    tiempo_ms        = Column(Integer, nullable=True)
    created_at       = Column(DateTime)
    updated_at       = Column(DateTime)


class Borrador(Base):
    __tablename__ = "borradores"

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    generacion_id  = Column(UUID(as_uuid=True), ForeignKey("generaciones.id", ondelete="CASCADE"), nullable=False)
    expediente_id  = Column(UUID(as_uuid=True), nullable=True)
    editado_por    = Column(UUID(as_uuid=True), nullable=False)
    tipo_documento = Column(String(50), nullable=False)
    contenido_html = Column(Text, nullable=False)
    contenido_texto= Column(Text, nullable=True)
    estado         = Column(String(20), default="borrador")
    created_at     = Column(DateTime)
    updated_at     = Column(DateTime)
