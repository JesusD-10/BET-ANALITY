from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.db import init_database


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        init_database()
        logger.info("Base de datos conectada y esquema inicializado correctamente.")
    except Exception:
        # The live provider chain can still serve requests during a temporary
        # database outage; Render logs retain the actionable connection error.
        logger.exception("No se pudo inicializar la base de datos.")
    yield


app = FastAPI(
    title="BET ANALIZADOR API",
    version="0.1.0",
    description="API informativa para analisis deportivo de futbol.",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(router, prefix="/api/v1")
print("ENVIRONMENT:", settings.environment)
print("CORS:", settings.allowed_origins)
print("API URL:", settings.next_public_api_url)
