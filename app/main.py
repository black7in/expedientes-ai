from fastapi import FastAPI
from .routers import admin, catalogo, documentos, plantillas

app = FastAPI(
    title="Expedientes Jurídicos — Servicio IA",
    description="Extracción de texto, NER y búsqueda semántica para expedientes jurídicos",
    version="0.4.0",
)

app.include_router(documentos.router)
app.include_router(plantillas.router)
app.include_router(catalogo.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"service": "expedientes-ai", "version": "0.4.0", "status": "running"}
