from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.schemas.health import HealthResponse
from app.schemas.matches import AssistantQuestion, AssistantResponse, MatchAnalysisResponse, MatchListResponse, RecommendationResponse
from app.core.config import settings
from app.services.matches import get_analysis, get_highlights, get_recommendations, search_matches

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="bet-analizador-api")


def _active_source() -> str:
    provider = settings.sports_data_provider.casefold()
    if provider in {"api-football", "apifootball"} and settings.api_football_key:
        return "api-football"
    if provider == "football-data" and settings.football_data_api_token:
        return "football-data"
    return "mock"



@router.get("/matches/highlights", response_model=MatchListResponse, tags=["matches"])
def highlights(match_date: date | None = Query(default=None)) -> MatchListResponse:
    selected_date = match_date or date.today()
    source = _active_source()
    return MatchListResponse(
        date=selected_date,
        matches=get_highlights(selected_date),
        source=source,
        notice=None if source != "mock" else "Proveedor externo no configurado; se muestran fixtures demostrativos.",
    )


@router.get("/matches/search", response_model=MatchListResponse, tags=["matches"])
def search(q: str | None = Query(default=None, max_length=120)) -> MatchListResponse:
    source = _active_source()
    return MatchListResponse(
        date=date.today(),
        matches=search_matches(q),
        source=source,
        notice=None if source != "mock" else "Proveedor externo no configurado; la búsqueda usa el catálogo mock.",
    )


@router.get("/matches/{match_id}/analysis", response_model=MatchAnalysisResponse, tags=["analysis"])
def analysis(match_id: str) -> MatchAnalysisResponse:
    result = get_analysis(match_id)
    if result is None:
        raise HTTPException(status_code=404, detail="El partido solicitado no existe")
    return result


@router.get("/recommendations/daily", response_model=RecommendationResponse, tags=["recommendations"])
def daily_recommendations() -> RecommendationResponse:
    return RecommendationResponse(recommendations=get_recommendations())


@router.get("/recommendations/dreams", response_model=RecommendationResponse, tags=["recommendations"])
def dream_recommendations() -> RecommendationResponse:
    dreams = [item for item in get_recommendations() if item.best_odds and item.best_odds >= 1.7]
    return RecommendationResponse(recommendations=dreams[:2])


@router.post("/assistant/question", response_model=AssistantResponse, tags=["assistant"])
def assistant_question(payload: AssistantQuestion) -> AssistantResponse:
    analysis_data = get_analysis(payload.match_id) if payload.match_id else None
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
