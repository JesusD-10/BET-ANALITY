from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.schemas.health import HealthResponse
from app.schemas.matches import AssistantQuestion, AssistantResponse, MatchAnalysisResponse, MatchListResponse, RecommendationResponse
from app.core.config import settings
from app.services.matches import (
    MatchProviderUnavailable,
    get_analysis,
    get_dream_recommendations,
    get_highlights_result,
    get_recommendations,
    search_matches_result,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="bet-analizador-api")


@router.get("/matches/highlights", response_model=MatchListResponse, tags=["matches"])
def highlights(match_date: date | None = Query(default=None)) -> MatchListResponse:
    result = get_highlights_result(match_date)
    return MatchListResponse(
        date=result.date,
        matches=result.matches,
        source=result.source,
        notice=result.notice,
    )


@router.get("/matches/search", response_model=MatchListResponse, tags=["matches"])
def search(q: str | None = Query(default=None, max_length=120)) -> MatchListResponse:
    result = search_matches_result(q)
    return MatchListResponse(
        date=result.date,
        matches=result.matches,
        source=result.source,
        notice=result.notice,
    )


@router.get("/matches/{match_id}/analysis", response_model=MatchAnalysisResponse, tags=["analysis"])
def analysis(match_id: str) -> MatchAnalysisResponse:
    try:
        result = get_analysis(match_id)
    except MatchProviderUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="El proveedor del partido está tardando. Intenta nuevamente en unos segundos.",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="El partido solicitado no existe")
    return result


@router.get("/recommendations/daily", response_model=RecommendationResponse, tags=["recommendations"])
def daily_recommendations(limit: int = Query(default=8, ge=1, le=20)) -> RecommendationResponse:
    return RecommendationResponse(recommendations=get_recommendations(limit=limit))


@router.get("/recommendations/dreams", response_model=RecommendationResponse, tags=["recommendations"])
def dream_recommendations(limit: int = Query(default=6, ge=1, le=20)) -> RecommendationResponse:
    return RecommendationResponse(recommendations=get_dream_recommendations(limit=limit))


@router.post("/assistant/question", response_model=AssistantResponse, tags=["assistant"])
def assistant_question(payload: AssistantQuestion) -> AssistantResponse:
    # The assistant reuses a local analysis so one user action triggers at most
    # one OpenAI request and remains inside the interactive timeout budget.
    analysis_data = get_analysis(payload.match_id, use_openai=False) if payload.match_id else None
    match_label = "el partido seleccionado" if analysis_data is None else f"{analysis_data.match.home_team} - {analysis_data.match.away_team}"
    context = "{}"
    if analysis_data is not None:
        context = analysis_data.model_dump_json()
    local_summary = f"Para {match_label}, el modelo destaca mercados con probabilidad estimada y cuota justa calculada. La respuesta usa datos observados del MVP y no sustituye una decision propia."
    source = "fallback-local"
    summary = local_summary
    if settings.openai_api_key:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=settings.openai_api_key,
                timeout=settings.openai_timeout_seconds,
                max_retries=settings.openai_max_retries,
            )
            response = client.responses.create(
                model=settings.openai_model,
                instructions="Explica usando exclusivamente los datos entregados. No inventes estadísticas, probabilidades o cuotas. No prometas resultados.",
                input=(
                    f"Pregunta del usuario: {payload.question}\n"
                    f"Resumen: {local_summary}\n"
                    f"Datos estructurados del partido: {context}"
                ),
            )
            summary = response.output_text or local_summary
            source = "openai"
        except Exception:
            summary = local_summary
    return AssistantResponse(
        summary=summary,
        factors_for=["Probabilidades calibradas del modelo baseline", "Calidad de datos visible por mercado"],
        factors_against=["Alineaciones no confirmadas", "Los datos son demostrativos mientras se integra un proveedor real"],
        data_limitations=["No se han conectado proveedores externos en esta version"],
        responsible_note="Un valor esperado positivo es una estimacion, no una garantia de resultado.",
        source=source,
    )
