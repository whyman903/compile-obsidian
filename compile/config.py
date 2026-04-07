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
    workspace_root: Path = field(default_factory=lambda: Path.cwd())

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

    return Config(
        topic=data.get("topic", "Untitled"),
        description=data.get("description", ""),
        workspace_root=root,
    )


def save_config(config: Config) -> None:
    config.compile_dir.mkdir(parents=True, exist_ok=True)
    data = {"topic": config.topic, "description": config.description}
    config.config_path.write_text(yaml.safe_dump(data, sort_keys=False))
