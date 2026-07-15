"""Regression tests for current Azure OpenAI and Foundry API behavior."""

from types import SimpleNamespace
from typing import Any

import pytest

from src import clients
from src.config import is_reasoning, is_v1
from src.evaluate.foundry import FoundryEvalsClient
from samples.rag_pipeline import upload_to_foundry


@pytest.mark.parametrize(
    "model",
    [
        "gpt-5.5",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-chat-latest",
        "codex-mini",
        "o3",
        "o4-mini",
    ],
)
def test_current_reasoning_models_use_v1_api(model: str) -> None:
    assert is_v1(model)
    assert is_reasoning(model)


def test_v1_client_uses_callable_entra_provider_and_v1_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = lambda: "token"
    requested_scopes: list[str] = []

    def fake_token_provider(scope: str) -> Any:
        requested_scopes.append(scope)
        return provider

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(clients, "_get_token_provider", fake_token_provider)
    monkeypatch.setattr(clients, "OpenAI", FakeOpenAI)

    client = clients.create_client("gpt-5.5", endpoint="https://example.openai.azure.com")

    assert requested_scopes == ["https://ai.azure.com/.default"]
    assert client.kwargs["api_key"] is provider


def test_legacy_client_keeps_cognitive_services_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = lambda: "token"
    requested_scopes: list[str] = []

    def fake_token_provider(scope: str) -> Any:
        requested_scopes.append(scope)
        return provider

    class FakeAzureOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(clients, "_get_token_provider", fake_token_provider)
    monkeypatch.setattr(clients, "AzureOpenAI", FakeAzureOpenAI)

    client = clients.create_client("gpt-4o", endpoint="https://example.openai.azure.com")

    assert requested_scopes == ["https://cognitiveservices.azure.com/.default"]
    assert client.kwargs["azure_ad_token_provider"] is provider


@pytest.mark.parametrize("model", ["gpt-5.3-codex", "codex-mini"])
def test_chat_helper_rejects_responses_only_model(model: str) -> None:
    with pytest.raises(ValueError, match="Responses API"):
        clients.call_model(object(), model, [{"role": "user", "content": "Hello"}])


def test_foundry_evaluator_uses_model_initialization_parameter() -> None:
    class FakeEvals:
        def __init__(self) -> None:
            self.create_kwargs: dict[str, Any] = {}

        def create(self, **kwargs: Any) -> SimpleNamespace:
            self.create_kwargs = kwargs
            return SimpleNamespace(id="eval-1")

    fake_evals = FakeEvals()
    foundry_client = FoundryEvalsClient(
        endpoint="https://example.services.ai.azure.com/api/projects/test",
        deployment_name="judge-deployment",
    )
    foundry_client._openai_client = SimpleNamespace(evals=fake_evals)

    foundry_client.create_eval("migration-eval", ["coherence"])

    criterion = fake_evals.create_kwargs["testing_criteria"][0]
    assert criterion["initialization_parameters"] == {"model": "judge-deployment"}


def test_rag_foundry_criteria_use_model_initialization_parameter() -> None:
    criteria = upload_to_foundry._build_testing_criteria("judge-deployment")

    for criterion in criteria:
        assert criterion["initialization_parameters"] == {"model": "judge-deployment"}
