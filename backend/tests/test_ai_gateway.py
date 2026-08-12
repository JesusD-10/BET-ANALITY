import pytest
import httpx
from threading import Barrier, get_ident

from app.core.config import Settings
from app.services.ai_gateway import (
    AIGateway,
    AIProvidersUnavailable,
    NoAIProviderConfigured,
    _completion_payload,
    _provider_specs,
)


def _settings(**overrides) -> Settings:
    values = {
        "xai_api_key": "",
        "deepseek_api_key": "",
        "cerebras_api_key": "",
        "github_models_token": "",
        "openrouter_api_key": "",
        "ai_provider_timeout_seconds": 2,
        "ai_total_timeout_seconds": 5,
        "ai_max_provider_attempts": 3,
        "ai_allow_paid_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_free_mode_routes_only_cerebras_and_openrouter_deterministically() -> None:
    gateway = AIGateway(
        _settings(
            xai_api_key="xai-key",
            deepseek_api_key="deepseek-key",
            cerebras_api_key="cerebras-key",
            openrouter_api_key="openrouter-key",
        )
    )

    first = gateway._ordered_providers("analysis", "fixture-123")
    second = gateway._ordered_providers("analysis", "fixture-123")

    assert [provider.name for provider in first] == [provider.name for provider in second]
    assert {provider.name for provider in first} == {"cerebras", "openrouter"}


def test_paid_providers_require_explicit_opt_in_even_with_keys() -> None:
    free_gateway = AIGateway(
        _settings(xai_api_key="xai-key", deepseek_api_key="deepseek-key")
    )
    paid_gateway = AIGateway(
        _settings(
            xai_api_key="xai-key",
            deepseek_api_key="deepseek-key",
            ai_allow_paid_providers=True,
        )
    )

    statuses = {status.name: status for status in free_gateway.provider_statuses()}

    assert free_gateway.is_available() is False
    assert statuses["xai"].state == "paid-disabled"
    assert statuses["deepseek"].state == "paid-disabled"
    assert {provider.name for provider in paid_gateway._live_providers()} == {
        "xai",
        "deepseek",
    }


def test_github_models_is_reported_as_retired_and_never_called() -> None:
    gateway = AIGateway(
        _settings(github_models_token="legacy-token", cerebras_api_key="live-key")
    )

    statuses = {status.name: status for status in gateway.provider_statuses()}
    routed = gateway._ordered_providers("general", "request")

    assert statuses["github"].state == "retired"
    assert "30-07-2026" in (statuses["github"].detail or "")
    assert [provider.name for provider in routed] == ["cerebras"]


def test_invalid_json_falls_through_to_next_provider(monkeypatch) -> None:
    gateway = AIGateway(
        _settings(cerebras_api_key="one", openrouter_api_key="two")
    )
    ordered = gateway._ordered_providers("analysis", "fixture-json")
    attempted: list[str] = []

    def fake_request(provider, messages, **kwargs):
        attempted.append(provider.name)
        if len(attempted) == 1:
            return "esto no es JSON", provider.model
        return '{"markets": []}', provider.model

    monkeypatch.setattr(gateway, "_request_provider", fake_request)

    completion = gateway.complete_json(
        [{"role": "user", "content": "analiza"}],
        task="analysis",
        routing_key="fixture-json",
    )

    assert attempted == [provider.name for provider in ordered]
    assert completion.provider == ordered[1].name
    assert completion.json_data == {"markets": []}


def test_provider_attempts_are_bounded(monkeypatch) -> None:
    gateway = AIGateway(
        _settings(
            xai_api_key="one",
            deepseek_api_key="two",
            cerebras_api_key="three",
            openrouter_api_key="four",
            ai_max_provider_attempts=2,
            ai_allow_paid_providers=True,
        )
    )
    attempted: list[str] = []

    def always_fail(provider, messages, **kwargs):
        attempted.append(provider.name)
        raise ValueError("invalid")

    monkeypatch.setattr(gateway, "_request_provider", always_fail)

    with pytest.raises(AIProvidersUnavailable):
        gateway.complete_text(
            [{"role": "user", "content": "consulta"}],
            task="assistant",
            routing_key="bounded",
        )

    assert len(attempted) == 2


def test_no_credentials_uses_explicit_unavailable_signal() -> None:
    gateway = AIGateway(_settings())

    assert gateway.is_available() is False
    with pytest.raises(NoAIProviderConfigured):
        gateway.complete_text(
            [{"role": "user", "content": "consulta"}],
            task="assistant",
            routing_key="none",
        )


def test_provider_spec_repr_never_contains_secret() -> None:
    provider = _provider_specs(_settings(xai_api_key="top-secret"))[0]

    assert "top-secret" not in repr(provider)


def test_cerebras_uses_current_completion_token_parameter() -> None:
    cerebras = next(
        provider
        for provider in _provider_specs(_settings(cerebras_api_key="key"))
        if provider.name == "cerebras"
    )

    payload = _completion_payload(
        cerebras,
        [{"role": "user", "content": "consulta"}],
        700,
        None,
    )

    assert payload["max_completion_tokens"] == 700
    assert "max_tokens" not in payload


def test_deepseek_flash_explicitly_disables_thinking() -> None:
    deepseek = next(
        provider
        for provider in _provider_specs(_settings(deepseek_api_key="key"))
        if provider.name == "deepseek"
    )

    payload = _completion_payload(
        deepseek,
        [{"role": "user", "content": "consulta"}],
        700,
        {"type": "json_object"},
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 700


def test_openrouter_requires_structured_output_capability() -> None:
    openrouter = next(
        provider
        for provider in _provider_specs(_settings(openrouter_api_key="key"))
        if provider.name == "openrouter"
    )

    payload = _completion_payload(
        openrouter,
        [{"role": "user", "content": "consulta"}],
        700,
        {"type": "json_object"},
    )

    assert payload["provider"] == {"require_parameters": True}


def test_analysis_consensus_calls_two_free_providers_in_parallel(monkeypatch) -> None:
    gateway = AIGateway(
        _settings(cerebras_api_key="one", openrouter_api_key="two")
    )
    barrier = Barrier(2)
    thread_ids: set[int] = set()

    def concurrent_request(provider, messages, **kwargs):
        thread_ids.add(get_ident())
        barrier.wait(timeout=1)
        return '{"markets": []}', provider.model

    monkeypatch.setattr(gateway, "_request_provider", concurrent_request)

    completions = gateway.complete_json_consensus(
        [{"role": "user", "content": "analiza"}],
        task="analysis",
        routing_key="parallel",
    )

    assert len(completions) == 2
    assert len(thread_ids) == 2
    assert [item.provider for item in completions] == [
        provider.name
        for provider in gateway._ordered_providers("analysis", "parallel")[:2]
    ]


def test_analysis_consensus_accepts_one_valid_provider(monkeypatch) -> None:
    gateway = AIGateway(
        _settings(cerebras_api_key="one", openrouter_api_key="two")
    )
    ordered = gateway._ordered_providers("analysis", "single-result")

    def one_failure(provider, messages, **kwargs):
        if provider.name == ordered[0].name:
            raise ValueError("invalid")
        return '{"markets": []}', provider.model

    monkeypatch.setattr(gateway, "_request_provider", one_failure)

    completions = gateway.complete_json_consensus(
        [{"role": "user", "content": "analiza"}],
        task="analysis",
        routing_key="single-result",
    )

    assert [item.provider for item in completions] == [ordered[1].name]


def test_http_adapter_uses_compatible_chat_endpoint(monkeypatch) -> None:
    gateway = AIGateway(_settings(xai_api_key="xai-secret"))
    xai = next(provider for provider in _provider_specs(gateway.config) if provider.name == "xai")
    captured: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, payload=json)
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "model": "grok-4.3",
                    "choices": [{"message": {"content": "respuesta"}}],
                },
            )

    monkeypatch.setattr("app.services.ai_gateway.httpx.Client", FakeClient)

    content, model = gateway._request_provider(
        xai,
        [{"role": "user", "content": "consulta"}],
        timeout=2,
        max_tokens=100,
        response_format=None,
    )

    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer xai-secret"
    assert captured["payload"]["max_tokens"] == 100
    assert content == "respuesta"
    assert model == "grok-4.3"
