"""The production chat model, read from settings."""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from cv_job_match.settings import Settings

logger = logging.getLogger(__name__)


def create_chat_model(settings: Settings) -> BaseChatModel | None:
    """Create the chat model of the OpenAI-compatible provider from settings.

    Args:
        settings: Settings with the provider's base URL, model and API key.

    Returns:
        The chat model, or ``None`` when the model or API key is not set.
    """
    if settings.llm_model is None or settings.llm_api_key is None:
        logger.warning("Chat model is not configured: model or API key is not set")
        return None
    logger.info("Using chat model %s at %s", settings.llm_model, settings.llm_base_url)
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
