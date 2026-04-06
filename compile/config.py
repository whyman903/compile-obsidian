from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

import yaml


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        # Override if not set or empty
        if not os.environ.get(key):
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    topic: str
    description: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    workspace_root: Path = field(default_factory=lambda: Path.cwd())
    temperature: float = 0.2
    max_analysis_chars: int = 20000
    chunk_target_chars: int = 4000
    vision_enabled: bool = True
    known_acronyms: tuple[str, ...] = ()
    fuzzy_match_threshold: float = 0.8
    query_token_budget: int = 180000
    debounce_seconds: float = 2.0

    @property
    def raw_dir(self) -> Path:
        return self.workspace_root / "raw"

    @property
    def wiki_dir(self) -> Path:
        return self.workspace_root / "wiki"

    @property
    def compile_dir(self) -> Path:
        return self.workspace_root / ".compile"

    @property
    def config_path(self) -> Path:
        return self.compile_dir / "config.yaml"

    @property
    def state_path(self) -> Path:
        return self.compile_dir / "state.json"

    @property
    def evidence_path(self) -> Path:
        return self.compile_dir / "evidence.json"

    @property
    def evidence_db_path(self) -> Path:
        return self.compile_dir / "evidence.db"

    @property
    def source_packets_dir(self) -> Path:
        return self.compile_dir / "source-packets"

    @property
    def quarantine_dir(self) -> Path:
        return self.compile_dir / "quarantine"

    @property
    def wiki_schema_path(self) -> Path:
        return self.workspace_root / "WIKI.md"


def load_config(workspace_root: Path | None = None) -> Config:
    root = workspace_root or Path.cwd()

    # Load .env from workspace root or parents
    for candidate in [root / ".env", root.parent / ".env", Path.home() / ".env"]:
        _load_dotenv(candidate)

    config_path = root / ".compile" / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No workspace found at {root}. Run 'compile init' first."
        )

    data = yaml.safe_load(config_path.read_text()) or {}

    # Parse known_acronyms: accept a list in YAML, store as tuple
    raw_acronyms = data.get("known_acronyms")
    if isinstance(raw_acronyms, list):
        known_acronyms = tuple(str(a).strip() for a in raw_acronyms if str(a).strip())
    elif isinstance(raw_acronyms, str) and raw_acronyms.strip():
        known_acronyms = tuple(a.strip() for a in raw_acronyms.split(",") if a.strip())
    else:
        known_acronyms = ()

    return Config(
        topic=data.get("topic", "Untitled"),
        description=data.get("description", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        workspace_root=root,
        temperature=float(data.get("temperature", 0.2)),
        max_analysis_chars=int(data.get("max_analysis_chars", 20000)),
        chunk_target_chars=int(data.get("chunk_target_chars", 4000)),
        vision_enabled=bool(data.get("vision_enabled", True)),
        known_acronyms=known_acronyms,
        fuzzy_match_threshold=float(data.get("fuzzy_match_threshold", 0.8)),
        query_token_budget=int(data.get("query_token_budget", 180000)),
        debounce_seconds=float(data.get("debounce_seconds", 2.0)),
    )


def save_config(config: Config) -> None:
    config.compile_dir.mkdir(parents=True, exist_ok=True)
    data = {"topic": config.topic, "description": config.description}
    config.config_path.write_text(yaml.safe_dump(data, sort_keys=False))
