from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings


app = FastAPI(
    title="BET ANALIZADOR API",
    version="0.1.0",
    description="API informativa para analisis deportivo de futbol.",
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