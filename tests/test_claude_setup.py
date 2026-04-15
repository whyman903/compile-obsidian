from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from compile.cli import main, install_claude_files
from compile.workspace import init_workspace


def _make_workspace(tmp_path: Path, name: str = "wiki") -> Path:
    ws = tmp_path / name
    init_workspace(ws, "Test Wiki", "A test workspace.")
    return ws


def _template_names(*parts: str) -> list[str]:
    template_dir = Path(__file__).resolve().parents[1] / "compile" / "templates"
    for part in parts:
        template_dir /= part
    return sorted(path.name for path in template_dir.iterdir() if path.is_file())


def test_fresh_install(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = install_claude_files(ws, home, force=False)

    global_templates = _template_names("global")
    workspace_templates = _template_names("workspace", "commands")

    assert len(result["installed"]) == len(global_templates) + len(workspace_templates) + 2
    assert result["skipped"] == []
    assert result["mispointed"] == []
    assert result["obsolete"] == []
    assert result["removed"] == []

    for name in global_templates:
        content = (home / ".claude" / "commands" / name).read_text()
        assert str(ws) in content
        assert "{{wiki_path}}" not in content
        assert "wiki-enrich" not in content

    wiki_query = (home / ".claude" / "commands" / "wiki-query.md").read_text()
    assert "markdown is the fallback" not in wiki_query
    assert "--nodes-file" in wiki_query
    assert "--script-file" in wiki_query
    assert "Want me to save this as a wiki output page?" in wiki_query
    assert "Only save it if the user says yes" in wiki_query

    workspace_claude = (ws / "CLAUDE.md").read_text()
    assert "markdown paragraphs are the fallback" in workspace_claude.lower()
    assert "compile render canvas" in workspace_claude
    assert "direct pdf or document understanding" in workspace_claude.lower()
    assert "compile source packet" not in workspace_claude
    assert "## Enrich Workflow" not in workspace_claude
    assert "<!-- compile:figures:start -->" not in workspace_claude
    assert "create them when the user asks for them or explicitly agrees" in workspace_claude.lower()
    assert (ws / ".claude" / "settings.local.json").exists()
    settings_content = (ws / ".claude" / "settings.local.json").read_text()
    assert '"mcp__notion"' in settings_content
    assert '"Edit(raw/notion/**)"' in settings_content
    assert '"Bash(compile health)"' in settings_content
    for name in workspace_templates:
        assert (ws / ".claude" / "commands" / name).exists()


def test_skip_existing_without_force(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)
    result = install_claude_files(ws, home, force=False)

    assert result["installed"] == []
    assert len(result["skipped"]) == len(_template_names("global")) + len(_template_names("workspace", "commands")) + 2
    assert result["mispointed"] == []
    assert result["obsolete"] == []
    assert result["removed"] == []


def test_force_overwrites(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)
    result = install_claude_files(ws, home, force=True)

    assert len(result["installed"]) == len(_template_names("global")) + len(_template_names("workspace", "commands")) + 2
    assert result["skipped"] == []
    assert result["mispointed"] == []
    assert result["obsolete"] == []
    assert result["removed"] == []


def test_force_merges_existing_settings_local_json(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)
    settings_path = ws / ".claude" / "settings.local.json"
    settings_path.write_text(
        """{
  "permissions": {
    "allow": [
      "Bash(custom command)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "custom",
        "hooks": [
          {
            "type": "command",
            "command": "echo custom"
          }
        ]
      }
    ]
  }
}
"""
    )

    install_claude_files(ws, home, force=True)

    merged = settings_path.read_text()
    assert '"Bash(custom command)"' in merged
    assert '"mcp__notion"' in merged
    assert '"matcher": "custom"' in merged
    assert "No wiki index found." in merged


def test_mispointed_globals_detected(tmp_path: Path) -> None:
    ws_a = _make_workspace(tmp_path, "wiki-a")
    ws_b = _make_workspace(tmp_path, "wiki-b")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws_a, home, force=False)
    result = install_claude_files(ws_b, home, force=False)

    assert len(result["mispointed"]) == len(_template_names("global"))
    local_installed = [f for f in result["installed"] if str(ws_b) in f]
    assert len(local_installed) == len(_template_names("workspace", "commands")) + 2


def test_mispointed_globals_detected_with_prefix_overlap(tmp_path: Path) -> None:
    ws_old = _make_workspace(tmp_path, "wiki-old")
    ws_new = _make_workspace(tmp_path, "wiki")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws_old, home, force=False)
    result = install_claude_files(ws_new, home, force=False)

    assert len(result["mispointed"]) == len(_template_names("global"))


def test_mispointed_globals_fixed_with_force(tmp_path: Path) -> None:
    ws_a = _make_workspace(tmp_path, "wiki-a")
    ws_b = _make_workspace(tmp_path, "wiki-b")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws_a, home, force=False)
    result = install_claude_files(ws_b, home, force=True)

    assert result["mispointed"] == []
    for name in _template_names("global"):
        content = (home / ".claude" / "commands" / name).read_text()
        assert str(ws_b) in content
        assert str(ws_a) not in content


def test_path_with_spaces_quoted_in_shell_commands(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path, "My Wiki")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)

    for name in _template_names("global"):
        content = (home / ".claude" / "commands" / name).read_text()
        assert f'cd "{ws}"' in content
        for line in content.splitlines():
            if line.strip().startswith(("cd ", "`cd ")):
                assert f'cd "{ws}"' in line, f"Unquoted path in shell command: {line}"


def test_obsolete_managed_files_reported_without_force(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    old_global = home / ".claude" / "commands"
    old_global.mkdir(parents=True)
    (old_global / "wiki-enrich.md").write_text("old")
    old_workspace = ws / ".claude" / "commands"
    old_workspace.mkdir(parents=True)
    (old_workspace / "enrich.md").write_text("old")

    result = install_claude_files(ws, home, force=False)

    assert str(old_global / "wiki-enrich.md") in result["obsolete"]
    assert str(old_workspace / "enrich.md") in result["obsolete"]
    assert result["removed"] == []
    assert (old_global / "wiki-enrich.md").exists()
    assert (old_workspace / "enrich.md").exists()


def test_force_removes_obsolete_managed_files(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    old_global = home / ".claude" / "commands"
    old_global.mkdir(parents=True)
    (old_global / "wiki-enrich.md").write_text("old")
    old_workspace = ws / ".claude" / "commands"
    old_workspace.mkdir(parents=True)
    (old_workspace / "enrich.md").write_text("old")

    result = install_claude_files(ws, home, force=True)

    assert str(old_global / "wiki-enrich.md") in result["removed"]
    assert str(old_workspace / "enrich.md") in result["removed"]
    assert not (old_global / "wiki-enrich.md").exists()
    assert not (old_workspace / "enrich.md").exists()


def test_no_obsolete_templates_installed(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)

    assert not (home / ".claude" / "commands" / "wiki-visualize.md").exists()
    assert not (ws / ".claude" / "commands" / "visualize.md").exists()
    assert not (home / ".claude" / "commands" / "wiki-enrich.md").exists()
    assert not (ws / ".claude" / "commands" / "enrich.md").exists()


def test_ingest_template_matches_simplified_workflow(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    install_claude_files(ws, home, force=False)
    ingest_content = (ws / ".claude" / "commands" / "ingest.md").read_text()
    assert "compile source packet" not in ingest_content
    assert "/enrich" not in ingest_content
    assert "<!-- compile:figures:start -->" not in ingest_content
    assert "compile render chart" in ingest_content
    assert "compile suggest maps" in ingest_content
    assert "workspace `CLAUDE.md`" in ingest_content
    assert "create it only if the user asks for it or explicitly agrees" in ingest_content.lower()
    assert "Connection Audit" not in ingest_content
    assert "## Topic Hubs" not in ingest_content


def test_install_covers_all_current_template_files(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)

    installed_globals = sorted(path.name for path in (home / ".claude" / "commands").iterdir() if path.is_file())
    installed_workspace_commands = sorted(
        path.name for path in (ws / ".claude" / "commands").iterdir() if path.is_file()
    )

    assert installed_globals == _template_names("global")
    assert installed_workspace_commands == _template_names("workspace", "commands")


def test_invalid_workspace_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["claude", "setup", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "No compile workspace" in result.output


def test_cli_reports_mispointed(tmp_path: Path, monkeypatch: object) -> None:
    ws_a = _make_workspace(tmp_path, "wiki-a")
    ws_b = _make_workspace(tmp_path, "wiki-b")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    runner = CliRunner()
    runner.invoke(main, ["claude", "setup", str(ws_a)])
    result = runner.invoke(main, ["claude", "setup", str(ws_b)])

    assert "point at a different wiki" in result.output
    assert "--force" in result.output


def test_cli_reports_obsolete_files(tmp_path: Path, monkeypatch: object) -> None:
    ws = _make_workspace(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    global_dir = fake_home / ".claude" / "commands"
    global_dir.mkdir(parents=True)
    (global_dir / "wiki-enrich.md").write_text("old")
    workspace_dir = ws / ".claude" / "commands"
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "enrich.md").write_text("old")

    runner = CliRunner()
    result = runner.invoke(main, ["claude", "setup", str(ws)])

    assert "Obsolete managed file(s) detected" in result.output
    assert "Re-run with --force to remove them" in result.output
