"""Test fixtures and pytest config."""

from __future__ import annotations

import pytest

from lit_review.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_api_key="",
        llm_base_url="https://api.openai.com/v1",
        llm_model="test-model",
        request_timeout=10.0,
        user_agent="LiteratureReviewAgent/0.1 (mailto:test@example.com)",
    )
