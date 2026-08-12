from collections import deque
from datetime import date
from ipaddress import IPv4Address, IPv6Address, ip_address
from math import ceil
from threading import Lock
import time

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas.health import HealthResponse
from app.schemas.matches import AssistantQuestion, AssistantResponse, MatchAnalysisResponse, MatchListResponse, RecommendationResponse
from app.services.ai_gateway import ai_gateway
from app.services.matches import (
    MatchProviderUnavailable,
    get_analysis,
    get_assistant_analysis_context,
    get_dream_recommendations,
    get_highlights_result,
    get_recommendations,
    search_matches_result,
)

router = APIRouter()

_ASSISTANT_RATE_LIMIT = 10
_ASSISTANT_RATE_WINDOW_SECONDS = 60
_ASSISTANT_CACHE_TTL_SECONDS = 5 * 60
_ASSISTANT_CACHE_MAX_ENTRIES = 2048
_assistant_guard = Lock()
_assistant_request_times: dict[str, deque[float]] = {}
_assistant_response_cache: dict[tuple[str, str], tuple[float, AssistantResponse]] = {}


def _parse_ip(value: str | None) -> IPv4Address | IPv6Address | None:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = ip_address(candidate)
    except ValueError:
        return None
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _assistant_client_key(request: Request) -> str:
    """Use forwarded IPs only when the immediate peer looks like a local proxy.

    A malformed or user-controlled forwarding chain must not create arbitrary
    rate-limit buckets. Render terminates traffic at an internal proxy, so the
    right-most public address is the conservative client candidate there.
    """

    peer = _parse_ip(request.client.host if request.client else None)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer is not None and (peer.is_private or peer.is_loopback):
        parts = [part.strip() for part in forwarded.split(",")]
        if 1 <= len(parts) <= 8:
            forwarded_ips = [_parse_ip(part) for part in parts]
            if all(item is not None for item in forwarded_ips):
                for item in reversed(forwarded_ips):
                    if item is not None and item.is_global:
                        return item.compressed
    return peer.compressed if peer is not None else "unknown-client"


def _enforce_assistant_rate_limit(request: Request, now: float) -> None:
    client_key = _assistant_client_key(request)
    cutoff = now - _ASSISTANT_RATE_WINDOW_SECONDS
    retry_after = 0
    with _assistant_guard:
        request_times = _assistant_request_times.setdefault(client_key, deque())
        while request_times and request_times[0] <= cutoff:
            request_times.popleft()
        if len(request_times) >= _ASSISTANT_RATE_LIMIT:
            retry_after = max(1, ceil(request_times[0] + _ASSISTANT_RATE_WINDOW_SECONDS - now))
        else:
            request_times.append(now)

        # Opportunistic cleanup prevents an unbounded map of inactive clients.
        if len(_assistant_request_times) > _ASSISTANT_CACHE_MAX_ENTRIES:
            for key, timestamps in list(_assistant_request_times.items()):
                if not timestamps or timestamps[-1] <= cutoff:
                    _assistant_request_times.pop(key, None)

    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Demasiadas consultas al asistente. Intenta nuevamente en un momento.",
            headers={"Retry-After": str(retry_after)},
        )


def _assistant_cache_key(payload: AssistantQuestion) -> tuple[str, str]:
    normalized_question = " ".join(payload.question.casefold().split())
    return payload.match_id or "", normalized_question


def _get_cached_assistant_response(
    cache_key: tuple[str, str],
    now: float,
) -> AssistantResponse | None:
    with _assistant_guard:
        cached = _assistant_response_cache.get(cache_key)
        if cached is None:
            return None
        stored_at, response = cached
        if now - stored_at > _ASSISTANT_CACHE_TTL_SECONDS:
            _assistant_response_cache.pop(cache_key, None)
            return None
        return response.model_copy(deep=True)


def _cache_assistant_response(
    cache_key: tuple[str, str],
    response: AssistantResponse,
    now: float,
) -> None:
    with _assistant_guard:
        expired_before = now - _ASSISTANT_CACHE_TTL_SECONDS
        for key, (stored_at, _) in list(_assistant_response_cache.items()):
            if stored_at <= expired_before:
                _assistant_response_cache.pop(key, None)
        if len(_assistant_response_cache) >= _ASSISTANT_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _assistant_response_cache,
                key=lambda key: _assistant_response_cache[key][0],
            )
            _assistant_response_cache.pop(oldest_key, None)
        _assistant_response_cache[cache_key] = (now, response.model_copy(deep=True))


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
def assistant_question(payload: AssistantQuestion, request: Request) -> AssistantResponse:
    now = time.monotonic()
    _enforce_assistant_rate_limit(request, now)
    cache_key = _assistant_cache_key(payload)
    cached_response = _get_cached_assistant_response(cache_key, now)
    if cached_response is not None:
        return cached_response

    # This helper only reuses in-memory match/analysis data. The assistant must
    # never start a second round of sports-provider calls.
    analysis_data = get_assistant_analysis_context(payload.match_id)
    match_label = "el partido seleccionado" if analysis_data is None else f"{analysis_data.match.home_team} - {analysis_data.match.away_team}"
    context = "{}"
    if analysis_data is not None:
        context = analysis_data.model_dump_json()
    local_summary = f"Para {match_label}, el modelo destaca mercados con probabilidad estimada y cuota justa calculada. La respuesta usa datos observados del MVP y no sustituye una decision propia."
    source = "fallback-local"
    summary = local_summary
    if ai_gateway.is_available():
        try:
            completion = ai_gateway.complete_text(
                messages=[
                    {
                        "role": "system",
                        "content": "Explica usando exclusivamente los datos entregados. No inventes estadísticas, probabilidades o cuotas. No prometas resultados.",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Pregunta del usuario: {payload.question}\n"
                            f"Resumen: {local_summary}\n"
                            f"Datos estructurados del partido: {context}"
                        ),
                    },
                ],
                task="assistant",
                routing_key=f"{payload.match_id or 'general'}:{payload.question}",
            )
            summary = completion.content or local_summary
            source = f"multi-ai-{completion.provider}"
        except Exception:
            summary = local_summary
    response = AssistantResponse(
        summary=summary,
        factors_for=["Probabilidades calibradas del modelo baseline", "Calidad de datos visible por mercado"],
        factors_against=["Alineaciones no confirmadas", "Los datos son demostrativos mientras se integra un proveedor real"],
        data_limitations=["La cobertura depende de los datos deportivos y de la cuota disponible del proveedor IA"],
        responsible_note="Un valor esperado positivo es una estimacion, no una garantia de resultado.",
        source=source,
    )
    _cache_assistant_response(cache_key, response, time.monotonic())
    return response
