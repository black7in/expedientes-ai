# expedientes-ai

Servicio de inteligencia artificial para el Sistema de Gestión de Expedientes Jurídicos.

## Responsabilidades
- Extracción de texto de documentos PDF (PyMuPDF) y Word (python-docx)
- Reconocimiento de entidades nombradas NER (partes, juzgado, nro. expediente, fechas)
- Generación de documentos legales con IA Generativa (RAG)
- Búsqueda semántica con embeddings (pgvector)

## Stack
- Python 3.12 + FastAPI
- PyMuPDF, python-docx
- Docker
