"""Model provider selection kept separate from Agent tools and contracts."""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile, merge_profile
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from app.config import Settings


def _is_deepseek_model(model: Model | str) -> bool:
    """Identify DeepSeek by its provider-facing model name."""

    if isinstance(model, OpenAIChatModel):
        return model.model_name.startswith("deepseek-")
    if not isinstance(model, str):
        return False
    if ":" not in model:
        return model.startswith("deepseek-")
    provider, model_name = model.split(":", 1)
    return provider in {"deepseek", "openai"} and model_name.startswith(
        "deepseek-"
    )


def _deepseek_chat_profile(model_name: str) -> ModelProfile | None:
    """Keep DeepSeek semantics while using a configured compatible endpoint."""

    if not model_name.startswith("deepseek-"):
        return None
    return merge_profile(
        DeepSeekProvider.model_profile(model_name),
        # DeepSeek Chat Completions documents ``max_tokens``. PydanticAI's
        # OpenAI profile otherwise maps the same setting to
        # ``max_completion_tokens``.
        OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
    )


def composer_model_settings(model: Model | str, *, max_tokens: int) -> ModelSettings:
    """Return the provider cap and non-thinking policy for answer composition."""

    settings: ModelSettings = {
        "parallel_tool_calls": False,
        "max_tokens": max_tokens,
    }
    if _is_deepseek_model(model):
        # DeepSeek's OpenAI-compatible API uses this extra-body toggle. The
        # unified ``thinking=False`` setting would serialize as the unsupported
        # ``reasoning_effort=none`` value instead.
        settings["extra_body"] = {"thinking": {"type": "disabled"}}
    else:
        settings["thinking"] = False
    return settings


def model_supports_streaming(model: Model | str) -> bool:
    """Return whether a concrete model overrides PydanticAI's stream seam.

    A configured string model is intentionally treated as unknown: selecting
    a provider by name does not prove that its implementation can produce
    deltas.  PydanticAI's base ``Model.request_stream`` is a fail-closed
    ``NotImplementedError`` implementation, so comparing the concrete class
    method with the base method gives us a local capability check without
    making a probe request (or consuming a user turn twice).
    """

    if isinstance(model, str):
        return False
    return getattr(type(model), "request_stream", None) is not getattr(
        Model, "request_stream", None
    )


def build_model(settings: Settings) -> Model | str:
    """Build a direct provider or an OpenAI-compatible gateway model."""

    if settings.agent_base_url:
        model_name = settings.agent_model.removeprefix("openai:")
        model_name = model_name.removeprefix("deepseek:")
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                base_url=settings.agent_base_url,
                api_key=settings.agent_api_key,
            ),
            profile=_deepseek_chat_profile(model_name),
        )
    if settings.agent_model.startswith("deepseek:"):
        model_name = settings.agent_model.removeprefix("deepseek:")
        return OpenAIChatModel(
            model_name,
            provider=DeepSeekProvider(api_key=settings.agent_api_key),
            profile=_deepseek_chat_profile(model_name),
        )
    if settings.agent_model.startswith("openai:") and settings.agent_api_key:
        model_name = settings.agent_model.removeprefix("openai:")
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(api_key=settings.agent_api_key),
            profile=_deepseek_chat_profile(model_name),
        )
    return settings.agent_model
