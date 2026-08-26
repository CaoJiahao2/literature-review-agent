"""Settings (env / .env) for the agent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when the agent cannot start because of missing configuration."""


class Settings(BaseSettings):
    """Resolved from environment variables + optional `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM (any OpenAI-compatible endpoint). Required for the ReAct agent.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Agent loop controls.
    max_agent_steps: int = 12
    max_reflections: int = 1
    resume_memory: bool = False

    # Source limits (per query).
    arxiv_max_per_query: int = 15
    openalex_max_per_query: int = 15
    huggingface_max_per_query: int = 20
    semantic_scholar_max_per_query: int = 10
    crossref_max_per_query: int = 10
    huggingface_lookback_days: int = 7

    # Default sources enabled when --sources is omitted. Order is the search order.
    default_sources: str = "arxiv,openalex,huggingface"

    # HTTP.
    request_timeout: float = 30.0
    user_agent: str = "LiteratureReviewAgent/0.2 (mailto:agent@example.com)"

    # Cross-run memory.
    memory_dir: Path = Field(default=Path("~/.lit_review/memory"))

    # Default year window: last 5 years.
    default_year_window: int = Field(default=5)

    @property
    def resolved_memory_dir(self) -> Path:
        return self.memory_dir.expanduser().resolve()

    def enabled_sources(self) -> list[str]:
        """Parse `default_sources` into a list of enabled source names."""
        return [s.strip() for s in self.default_sources.split(",") if s.strip()]

    def year_window(self) -> tuple[int, int]:
        today = date.today().year
        return (today - self.default_year_window + 1, today)

    def has_llm(self) -> bool:
        return bool(self.llm_api_key.strip())


def load_settings() -> Settings:
    return Settings()
