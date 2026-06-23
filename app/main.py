from contextlib import asynccontextmanager

from fastapi import FastAPI
from .routers import admin, analisis, catalogo, documentos, expedientes_router, export, generacion, ner, pipeline, plantillas, recuperacion
from .services.ner_service import ner_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    ner_service.cargar_modelo()
    yield


app = FastAPI(
    title="Expedientes Jurídicos — Servicio IA",
    description="Extracción de texto, NER y búsqueda semántica para expedientes jurídicos",
    version="0.6.0",
    lifespan=lifespan,
)

app.include_router(documentos.router)
app.include_router(ner.router)
app.include_router(expedientes_router.router)
app.include_router(plantillas.router)
app.include_router(catalogo.router)
app.include_router(admin.router)
app.include_router(analisis.router)
app.include_router(recuperacion.router)
app.include_router(generacion.router)
app.include_router(pipeline.router)
app.include_router(export.router)


@app.get("/")
async def root():
    return {"service": "expedientes-ai", "version": "0.6.0", "status": "running"}
