from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_workspaces_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "workspaces"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    workspaces_dir: Path = Field(
        default_factory=_default_workspaces_dir,
        description="Directory for persistent git clones and local indexes.",
    )
    webhook_secret: str = Field(
        default="",
        description="If set, X-Hub-Signature-256 must validate (GitHub webhook secret).",
    )
    event_allowlist: str = Field(
        default="",
        description="Comma-separated GitHub event names; empty = all supported.",
    )
    repo_allowlist: str = Field(
        default="",
        description="Comma-separated owner/repo; empty = any repository.",
    )
    trigger_keywords: str = Field(
        default="/fix,/autofix,@jannus",
        description="Comma-separated substrings; issue_comment must contain one to trigger.",
    )
    claude_bin: str = Field(default="claude")
    claude_extra_args: str = Field(
        default="",
        description="Extra CLI args after -p, space-separated (e.g. --max-turns 20).",
    )
    claude_timeout: int = Field(default=600, ge=30, le=3600)
    webhook_dry_run: bool = Field(
        default=False,
        description="If true, skip git and claude; graph still runs with dry-run flags.",
    )
    max_attempts: int = Field(default=3, ge=1, le=10, description="Reviewer loop limit before human escalation.")
    rag_enabled: bool = Field(default=False, description="Enable LlamaIndex + Chroma RAG in prompt builder.")

    openai_api_key: str = Field(default="", description="OpenAI API key for planner and reviewer agents.")
    openai_model: str = Field(default="gpt-4o", description="Model for LangChain agents.")

    telegram_bot_token: str = Field(default="", description="Telegram bot token for notifier.")
    telegram_chat_id: str = Field(default="", description="Telegram chat id to send notifications.")

    langchain_tracing_v2: bool = Field(default=False)
    langchain_api_key: str = Field(default="")
    langchain_project: str = Field(default="jannus")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8765, ge=1, le=65535)

    def parsed_event_allowlist(self) -> set[str]:
        if not self.event_allowlist.strip():
            return set()
        return {x.strip().lower() for x in self.event_allowlist.split(",") if x.strip()}

    def parsed_repo_allowlist(self) -> set[str]:
        if not self.repo_allowlist.strip():
            return set()
        return {x.strip().lower() for x in self.repo_allowlist.split(",") if x.strip()}

    def parsed_trigger_keywords(self) -> list[str]:
        if not self.trigger_keywords.strip():
            return []
        return [x.strip() for x in self.trigger_keywords.split(",") if x.strip()]

    def claude_extra_argv(self) -> list[str]:
        if not self.claude_extra_args.strip():
            return []
        return self.claude_extra_args.split()

    @property
    def checkpoint_db_path(self) -> Path:
        return self.workspaces_dir / ".jannus_state.db"

    @property
    def registry_path(self) -> Path:
        return self.workspaces_dir / "registry.json"


_settings: Settings | None = None


def load_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
