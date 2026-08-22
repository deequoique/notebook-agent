import inspect
import json
from dataclasses import replace

import httpx
import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.function import FunctionModel

from app.agent.provider import build_model, composer_model_settings
from app.agent.runtime import ComposerDeps, build_composer
from app.agent.types import Citation
from app.bootstrap import build_embedding_provider, build_knowledge_agent
from app.agent.actions import AgentActionServices
from app.channels.pending_actions import PendingConfirmationService
from app.config import Settings
from app.ingest.tasks import create_item, ingest_url
from app.ingest.submission import IngestSubmissionService
from app.retrieval.search import bm25_search, vector_search
from app.tls import TrustedCA


def test_provider_gateway_configuration_is_isolated_from_agent_contracts():
    settings = replace(
        Settings(),
        agent_model="openai:compatible-model",
        agent_api_key="test-key",
        agent_base_url="http://127.0.0.1:9999/v1",
    )

    model = build_model(settings)

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "compatible-model"


@pytest.mark.asyncio
async def test_deepseek_composer_uses_real_cap_and_disables_thinking_on_wire():
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek-v4-flash",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "kind": "grounded",
                            "sections": [{
                                "text": "grounded",
                                "citation_ids": [3],
                            }]
                        }),
                    },
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    settings = replace(
        Settings(),
        agent_model="openai:deepseek-v4-flash",
        agent_api_key="test-key",
        agent_base_url="https://deepseek.example/v1",
    )
    model = build_model(settings)
    assert isinstance(model, OpenAIChatModel)
    model._provider._set_http_client(http_client)
    citation = Citation(
        item_id=2,
        segment_id=3,
        title="source",
        excerpt="evidence",
        url="https://example.test",
    )
    try:
        result = await build_composer(model).run(
            "question",
            deps=ComposerDeps({3: citation}),
            model_settings=composer_model_settings(model, max_tokens=1000),
        )
    finally:
        await http_client.aclose()

    assert result.output.sections[0].citation_ids == [3]
    assert len(requests) == 1
    body = requests[0]
    assert body["max_tokens"] == 1000
    assert body["thinking"] == {"type": "disabled"}
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body


def test_direct_deepseek_model_uses_the_same_chat_profile():
    model = build_model(replace(
        Settings(),
        agent_model="deepseek:deepseek-v4-flash",
        agent_api_key="test-key",
        agent_base_url=None,
    ))

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "deepseek-v4-flash"
    assert model.profile["openai_chat_supports_max_completion_tokens"] is False
    assert model.profile["openai_chat_thinking_field"] == "reasoning_content"


def test_generic_composer_requests_provider_neutral_non_thinking():
    model = FunctionModel(lambda _messages, _info: None)

    settings = composer_model_settings(model, max_tokens=1000)

    assert settings["max_tokens"] == 1000
    assert settings["thinking"] is False
    assert "extra_body" not in settings


def test_query_embedding_provider_receives_the_resolved_ca_context():
    context = object()
    provider = build_embedding_provider(
        replace(Settings(), zhipu_api_key="test-key"),
        trusted_ca=TrustedCA(bundle_path="/safe/ca.pem", ssl_context=context),
    )

    assert provider is not None
    assert provider._ssl_context is context


def test_ingest_and_retrieval_require_explicit_user_id():
    for function in (create_item, ingest_url, bm25_search, vector_search):
        parameter = inspect.signature(function).parameters["user_id"]
        assert parameter.default is inspect.Parameter.empty


def test_action_services_are_always_composed():
    factory = lambda: None
    agent = build_knowledge_agent(
        replace(
            Settings(),
            zhipu_api_key=None,
        ),
        session_factory=factory,
    )

    services = agent._action_factory(None)

    assert isinstance(services, AgentActionServices)
    assert isinstance(services.submission, IngestSubmissionService)
    assert isinstance(services.pending, PendingConfirmationService)
