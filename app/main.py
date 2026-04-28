from fastapi import FastAPI
from .routers import documentos

app = FastAPI(
    title="Expedientes Jurídicos — Servicio IA",
    description="Extracción de texto, NER y búsqueda semántica para expedientes jurídicos",
    version="0.1.0",
)

app.include_router(documentos.router)


@app.get("/")
def root():
    return {"service": "expedientes-ai", "version": "0.1.0", "status": "running"}
