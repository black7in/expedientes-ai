from fastapi import FastAPI
from .routers import admin, documentos, generacion, indexar

app = FastAPI(
    title="Expedientes Jurídicos — Servicio IA",
    description="Extracción de texto, NER y búsqueda semántica para expedientes jurídicos",
    version="0.2.0",
)

app.include_router(documentos.router)
app.include_router(generacion.router)
app.include_router(indexar.router)
app.include_router(admin.router)


@app.get("/")
def root():
    return {"service": "expedientes-ai", "version": "0.2.0", "status": "running"}
