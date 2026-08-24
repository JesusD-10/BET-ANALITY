"""Deterministic, fault-tolerant routing for chat-completions-compatible APIs.

Text tasks use one provider with sequential fallback. Match analysis can query
up to four providers in parallel for contrast. Both paths share a hard deadline
so fault tolerance never multiplies the latency visible to the user.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import hashlib
import json
import logging
import time
from typing import Any, Callable

import httpx

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

AIMessage = dict[str, str]


class AIOrchestratorError(RuntimeError):
    """Base error for provider routing and response validation."""


class NoAIProviderConfigured(AIOrchestratorError):
    """No live provider has a configured credential."""


class AIProvidersUnavailable(AIOrchestratorError):
    """Every attempted provider failed or the common deadline elapsed."""


class InvalidAIResponse(AIOrchestratorError):
    """A provider response did not match the compatible chat contract."""


@dataclass(frozen=True)
class AIProviderSpec:
    name: str
    api_key: str = field(repr=False)
    base_url: str
    model: str
    token_parameter: str = "max_tokens"
    extra_headers: tuple[tuple[str, str], ...] = ()
    retired_reason: str | None = None


@dataclass(frozen=True)
class AICompletion:
    content: str
    provider: str
    model: str
    json_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class AIProviderStatus:
    name: str
    state: str
    model: str
    detail: str | None = None


_TASK_PROVIDER_ORDER: dict[str, tuple[str, ...]] = {
    "analysis": ("cerebras", "deepseek", "groq", "openrouter"),
    "assistant": ("cerebras", "groq", "deepseek", "openrouter"),
    "general": ("cerebras", "groq", "deepseek", "openrouter"),
}

_PAID_PROVIDERS = frozenset({"deepseek"})


def _provider_specs(config: Settings) -> list[AIProviderSpec]:
    openrouter_headers: list[tuple[str, str]] = [("X-Title", config.app_name)]
    if config.openrouter_site_url:
        openrouter_headers.append(("HTTP-Referer", config.openrouter_site_url))

    return [
        AIProviderSpec(
            name="deepseek",
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            model=config.deepseek_model,
        ),
        AIProviderSpec(
            name="groq",
            api_key=config.groq_api_key,
            base_url=config.groq_base_url,
            model=config.groq_model,
            token_parameter="max_completion_tokens",
        ),
        AIProviderSpec(
            name="cerebras",
            api_key=config.cerebras_api_key,
            base_url=config.cerebras_base_url,
            model=config.cerebras_model,
            token_parameter="max_completion_tokens",
        ),
        AIProviderSpec(
            name="openrouter",
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url,
            model=config.openrouter_model,
            extra_headers=tuple(openrouter_headers),
        ),
    ]


def _chat_completions_url(base_url: str) -> str:
    clean_url = base_url.rstrip("/")
    if clean_url.endswith("/chat/completions"):
        return clean_url
    return f"{clean_url}/chat/completions"


def _completion_payload(
    provider: AIProviderSpec,
    messages: list[AIMessage],
    max_tokens: int,
    response_format: dict[str, str] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": provider.model,
        "messages": messages,
        "stream": False,
        "temperature": 0.2,
        provider.token_parameter: max_tokens,
    }
    if response_format is not None:
        payload["response_format"] = response_format
        if provider.name == "openrouter":
            # Free routing must select only backends that honor structured
            # output rather than silently dropping response_format.
            payload["provider"] = {"require_parameters": True}
    if provider.name == "deepseek":
        # DeepSeek V4 defaults to thinking mode. Flash is used for low-latency
        # analysis, so the official toggle is set explicitly on every call.
        payload["thinking"] = {"type": "disabled"}
    return payload


def _extract_message_content(payload: dict[str, Any]) -> tuple[str, str | None]:
    try:
        message = payload["choices"][0]["message"]
        content = message["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InvalidAIResponse("Respuesta sin choices[0].message.content") from exc

    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            value = part.get("text") or part.get("content")
            if isinstance(value, str):
                parts.append(value)
        text = "".join(parts).strip()
    else:
        text = ""

    if not text:
        raise InvalidAIResponse("El proveedor devolvió contenido vacío")
    actual_model = payload.get("model")
    return text, actual_model if isinstance(actual_model, str) else None


def _extract_json_object(content: str) -> dict[str, Any]:
    candidate = content.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline >= 0:
            candidate = candidate[first_newline + 1 :]
        if candidate.endswith("```"):
            candidate = candidate[:-3].rstrip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        object_start = candidate.find("{")
        if object_start < 0:
            raise InvalidAIResponse("El proveedor no devolvió JSON") from None
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate[object_start:])
        except json.JSONDecodeError as exc:
            raise InvalidAIResponse("El proveedor devolvió JSON inválido") from exc

    if not isinstance(parsed, dict):
        raise InvalidAIResponse("La respuesta JSON no es un objeto")
    return parsed


class AIGateway:
    def __init__(self, config: Settings = settings) -> None:
        self.config = config

    def provider_statuses(self) -> list[AIProviderStatus]:
        statuses: list[AIProviderStatus] = []
        for provider in _provider_specs(self.config):
            if provider.retired_reason:
                state = "retired"
                detail = provider.retired_reason
            elif not self.config.ai_enabled:
                state = "disabled"
                detail = "Motor multi-IA desactivado"
            elif (
                provider.api_key
                and provider.name in _PAID_PROVIDERS
                and not self.config.ai_allow_paid_providers
            ):
                state = "paid-disabled"
                detail = "Requiere AI_ALLOW_PAID_PROVIDERS=true"
            elif provider.api_key:
                state = "available"
                detail = None
            else:
                state = "unconfigured"
                detail = "Falta credencial"
            statuses.append(
                AIProviderStatus(
                    name=provider.name,
                    state=state,
                    model=provider.model,
                    detail=detail,
                )
            )
        return statuses

    def is_available(self) -> bool:
        return bool(self._live_providers())

    def _live_providers(self) -> list[AIProviderSpec]:
        if not self.config.ai_enabled:
            return []
        return [
            provider
            for provider in _provider_specs(self.config)
            if (
                provider.api_key
                and provider.retired_reason is None
                and (
                    provider.name not in _PAID_PROVIDERS
                    or self.config.ai_allow_paid_providers
                )
            )
        ]

    def _ordered_providers(self, task: str, routing_key: str) -> list[AIProviderSpec]:
        available = {provider.name: provider for provider in self._live_providers()}
        preferred_names = _TASK_PROVIDER_ORDER.get(task, _TASK_PROVIDER_ORDER["general"])
        ordered = [available[name] for name in preferred_names if name in available]
        if not ordered:
            return []

        digest = hashlib.sha256(f"{task}:{routing_key}".encode("utf-8")).digest()
        offset = int.from_bytes(digest[:4], "big") % len(ordered)
        return ordered[offset:] + ordered[:offset]

    def complete_text(
        self,
        messages: list[AIMessage],
        *,
        task: str,
        routing_key: str,
        max_tokens: int = 700,
    ) -> AICompletion:
        return self._complete(
            messages,
            task=task,
            routing_key=routing_key,
            max_tokens=max_tokens,
            response_format=None,
            validator=None,
        )

    def complete_json(
        self,
        messages: list[AIMessage],
        *,
        task: str,
        routing_key: str,
        max_tokens: int = 1800,
    ) -> AICompletion:
        return self._complete(
            messages,
            task=task,
            routing_key=routing_key,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            validator=_extract_json_object,
        )

    def complete_json_consensus(
        self,
        messages: list[AIMessage],
        *,
        task: str,
        routing_key: str,
        max_tokens: int = 1800,
        max_providers: int = 4,
    ) -> list[AICompletion]:
        """Query at most four providers concurrently under one shared deadline.

        Results are returned in deterministic routing order, never completion
        order. One valid result is sufficient; callers can contrast all results
        that finish successfully. There is no second wave of requests, keeping
        the total latency bounded by ``ai_total_timeout_seconds``.
        """

        providers = self._ordered_providers(task, routing_key)
        if not providers:
            raise NoAIProviderConfigured("No hay proveedores IA activos con credenciales")

        provider_limit = max(
            1,
            min(int(max_providers), 4, self.config.ai_max_provider_attempts),
        )
        selected = providers[:provider_limit]
        deadline = time.monotonic() + self.config.ai_total_timeout_seconds
        request_timeout = min(
            float(self.config.ai_provider_timeout_seconds),
            float(self.config.ai_total_timeout_seconds),
        )
        executor = ThreadPoolExecutor(
            max_workers=len(selected),
            thread_name_prefix="ai-consensus",
        )
        scheduled: list[tuple[AIProviderSpec, Future[AICompletion]]] = []
        try:
            for provider in selected:
                future = executor.submit(
                    self._complete_provider,
                    provider,
                    messages,
                    timeout=max(0.1, request_timeout),
                    max_tokens=max(1, min(int(max_tokens), 4000)),
                    response_format={"type": "json_object"},
                    validator=_extract_json_object,
                )
                scheduled.append((provider, future))

            remaining = max(0.0, deadline - time.monotonic())
            done, pending = wait(
                [future for _, future in scheduled],
                timeout=remaining,
            )
            for future in pending:
                future.cancel()

            completions: list[AICompletion] = []
            for provider, future in scheduled:
                if future not in done:
                    logger.warning(
                        "Proveedor IA %s excedió el plazo compartido.",
                        provider.name,
                    )
                    continue
                try:
                    completions.append(future.result())
                except Exception as exc:
                    logger.warning(
                        "Proveedor IA %s falló durante el consenso (%s).",
                        provider.name,
                        type(exc).__name__,
                    )
            if completions:
                return completions
        finally:
            # Running HTTP calls keep their own bounded transport timeout; do
            # not wait for one failed provider before returning valid peers.
            executor.shutdown(wait=False, cancel_futures=True)

        attempted = ", ".join(provider.name for provider in selected)
        raise AIProvidersUnavailable(
            f"Proveedores IA no disponibles dentro del presupuesto: {attempted}"
        )

    def _complete_provider(
        self,
        provider: AIProviderSpec,
        messages: list[AIMessage],
        *,
        timeout: float,
        max_tokens: int,
        response_format: dict[str, str] | None,
        validator: Callable[[str], dict[str, Any]] | None,
    ) -> AICompletion:
        content, actual_model = self._request_provider(
            provider,
            messages,
            timeout=timeout,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        parsed = validator(content) if validator else None
        return AICompletion(
            content=content,
            provider=provider.name,
            model=actual_model or provider.model,
            json_data=parsed,
        )

    def _complete(
        self,
        messages: list[AIMessage],
        *,
        task: str,
        routing_key: str,
        max_tokens: int,
        response_format: dict[str, str] | None,
        validator: Callable[[str], dict[str, Any]] | None,
    ) -> AICompletion:
        providers = self._ordered_providers(task, routing_key)
        if not providers:
            raise NoAIProviderConfigured("No hay proveedores IA activos con credenciales")

        deadline = time.monotonic() + self.config.ai_total_timeout_seconds
        attempted: list[str] = []
        for provider in providers[: self.config.ai_max_provider_attempts]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            timeout = min(float(self.config.ai_provider_timeout_seconds), remaining)
            attempted.append(provider.name)
            try:
                return self._complete_provider(
                    provider,
                    messages,
                    timeout=max(0.1, timeout),
                    max_tokens=max(1, min(int(max_tokens), 4000)),
                    response_format=response_format,
                    validator=validator,
                )
            except (httpx.HTTPError, InvalidAIResponse, ValueError) as exc:
                # Never include credentials, payloads or provider response bodies
                # in logs. The error class is enough for operational diagnosis.
                logger.warning(
                    "Proveedor IA %s falló (%s); probando fallback.",
                    provider.name,
                    type(exc).__name__,
                )

        attempted_text = ", ".join(attempted) or "ninguno"
        raise AIProvidersUnavailable(
            f"Proveedores IA no disponibles dentro del presupuesto: {attempted_text}"
        )

    def _request_provider(
        self,
        provider: AIProviderSpec,
        messages: list[AIMessage],
        *,
        timeout: float,
        max_tokens: int,
        response_format: dict[str, str] | None,
    ) -> tuple[str, str | None]:
        headers = {
            "Authorization": f"Bearer {provider.api_key}",
            "Content-Type": "application/json",
            **dict(provider.extra_headers),
        }
        payload = _completion_payload(
            provider,
            messages,
            max_tokens,
            response_format,
        )

        with httpx.Client(timeout=httpx.Timeout(timeout), follow_redirects=False) as client:
            response = client.post(
                _chat_completions_url(provider.base_url),
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            # Do not expose response bodies: providers sometimes echo request
            # metadata and they are unnecessary for fallback decisions.
            raise httpx.HTTPStatusError(
                f"Proveedor respondió HTTP {response.status_code}",
                request=response.request,
                response=response,
            )
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise InvalidAIResponse("El proveedor no devolvió JSON HTTP válido") from exc
        if not isinstance(response_payload, dict):
            raise InvalidAIResponse("La respuesta HTTP no es un objeto")
        return _extract_message_content(response_payload)


ai_gateway = AIGateway()
