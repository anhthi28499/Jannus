from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    repo_path: Path = Field(
        ...,
        description="Local clone path; service runs git pull here before Claude.",
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
        description="If true, log prompt only; skip git pull and claude (safe for local tests).",
    )
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


_settings: Settings | None = None


def load_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
