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
        "deepseek_api_key": "",
        "groq_api_key": "",
        "cerebras_api_key": "",
        "openrouter_api_key": "",
        "ai_provider_timeout_seconds": 2,
        "ai_total_timeout_seconds": 5,
        "ai_max_provider_attempts": 3,
        "ai_allow_paid_providers": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_free_mode_routes_groq_cerebras_and_openrouter_deterministically() -> None:
    gateway = AIGateway(
        _settings(
            deepseek_api_key="deepseek-key",
            groq_api_key="groq-key",
            cerebras_api_key="cerebras-key",
            openrouter_api_key="openrouter-key",
        )
    )

    first = gateway._ordered_providers("analysis", "fixture-123")
    second = gateway._ordered_providers("analysis", "fixture-123")

    assert [provider.name for provider in first] == [provider.name for provider in second]
    assert {provider.name for provider in first} == {"cerebras", "groq", "openrouter"}


def test_paid_providers_require_explicit_opt_in_even_with_keys() -> None:
    free_gateway = AIGateway(
        _settings(deepseek_api_key="deepseek-key")
    )
    paid_gateway = AIGateway(
        _settings(
            deepseek_api_key="deepseek-key",
            ai_allow_paid_providers=True,
        )
    )

    statuses = {status.name: status for status in free_gateway.provider_statuses()}

    assert free_gateway.is_available() is False
    assert statuses["deepseek"].state == "paid-disabled"
    assert {provider.name for provider in paid_gateway._live_providers()} == {"deepseek"}


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
            deepseek_api_key="two",
            groq_api_key="three",
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
    provider = next(
        provider
        for provider in _provider_specs(_settings(groq_api_key="top-secret"))
        if provider.name == "groq"
    )

    assert "top-secret" not in repr(provider)


def test_groq_settings_load_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "groq-from-env")
    monkeypatch.setenv("GROQ_BASE_URL", "https://groq.example/v1/")
    monkeypatch.setenv("GROQ_MODEL", "groq-test-model")

    configured = Settings(_env_file=None)
    groq = next(
        provider
        for provider in _provider_specs(configured)
        if provider.name == "groq"
    )

    assert groq.api_key == "groq-from-env"
    assert groq.base_url == "https://groq.example/v1/"
    assert groq.model == "groq-test-model"


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


def test_groq_uses_current_model_and_completion_token_parameter() -> None:
    groq = next(
        provider
        for provider in _provider_specs(_settings(groq_api_key="key"))
        if provider.name == "groq"
    )

    payload = _completion_payload(
        groq,
        [{"role": "user", "content": "consulta"}],
        700,
        {"type": "json_object"},
    )

    assert groq.model == "openai/gpt-oss-120b"
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


def test_analysis_consensus_calls_four_providers_in_parallel_and_keeps_order(
    monkeypatch,
) -> None:
    gateway = AIGateway(
        _settings(
            deepseek_api_key="deepseek",
            groq_api_key="groq",
            cerebras_api_key="cerebras",
            openrouter_api_key="openrouter",
            ai_max_provider_attempts=4,
            ai_allow_paid_providers=True,
        )
    )
    barrier = Barrier(4)
    thread_ids: set[int] = set()

    def concurrent_request(provider, messages, **kwargs):
        thread_ids.add(get_ident())
        barrier.wait(timeout=1)
        return '{"markets": []}', provider.model

    monkeypatch.setattr(gateway, "_request_provider", concurrent_request)

    completions = gateway.complete_json_consensus(
        [{"role": "user", "content": "analiza"}],
        task="analysis",
        routing_key="four-parallel",
        max_providers=99,
    )

    expected = gateway._ordered_providers("analysis", "four-parallel")
    assert len(thread_ids) == 4
    assert [item.provider for item in completions] == [item.name for item in expected]


def test_analysis_consensus_respects_attempt_cap_and_keeps_partial_results(
    monkeypatch,
) -> None:
    gateway = AIGateway(
        _settings(
            deepseek_api_key="deepseek",
            groq_api_key="groq",
            cerebras_api_key="cerebras",
            openrouter_api_key="openrouter",
            ai_max_provider_attempts=3,
            ai_allow_paid_providers=True,
        )
    )
    ordered = gateway._ordered_providers("analysis", "partial-wave")
    attempted: list[str] = []

    def partial_failure(provider, messages, **kwargs):
        attempted.append(provider.name)
        if provider.name == ordered[1].name:
            raise ValueError("invalid")
        return '{"markets": []}', provider.model

    monkeypatch.setattr(gateway, "_request_provider", partial_failure)

    completions = gateway.complete_json_consensus(
        [{"role": "user", "content": "analiza"}],
        task="analysis",
        routing_key="partial-wave",
        max_providers=4,
    )

    assert set(attempted) == {item.name for item in ordered[:3]}
    assert [item.provider for item in completions] == [
        ordered[0].name,
        ordered[2].name,
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
    gateway = AIGateway(_settings(groq_api_key="groq-secret"))
    groq = next(provider for provider in _provider_specs(gateway.config) if provider.name == "groq")
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
                    "model": "openai/gpt-oss-120b",
                    "choices": [{"message": {"content": "respuesta"}}],
                },
            )

    monkeypatch.setattr("app.services.ai_gateway.httpx.Client", FakeClient)

    content, model = gateway._request_provider(
        groq,
        [{"role": "user", "content": "consulta"}],
        timeout=2,
        max_tokens=100,
        response_format=None,
    )

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer groq-secret"
    assert captured["payload"]["max_completion_tokens"] == 100
    assert content == "respuesta"
    assert model == "openai/gpt-oss-120b"
