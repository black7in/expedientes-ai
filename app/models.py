import uuid
from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Documento(Base):
    __tablename__ = "documentos"

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expediente_id     = Column(UUID(as_uuid=True), nullable=True)
    nombre_archivo    = Column(String(255), nullable=False)
    formato           = Column(String(10), nullable=False)
    tipo_documento    = Column(String(50), nullable=False)
    estado_extraccion = Column(String(20), nullable=False, default="pendiente")
    texto_extraido    = Column(JSONB, nullable=True)
    created_at        = Column(DateTime, nullable=True)
    updated_at        = Column(DateTime, nullable=True)


class LeyChunk(Base):
    __tablename__ = "leyes_chunks"

    id              = Column(BigInteger, primary_key=True, autoincrement=True)
    ley             = Column(String(100), nullable=False)
    numero_articulo = Column(String(20), nullable=False)
    titulo_articulo = Column(Text, nullable=True)
    materia         = Column(String(50), nullable=False)
    chunk_texto     = Column(Text, nullable=False)
    created_at      = Column(DateTime)
    # embedding: vector(1024) — gestionado vía SQL raw
