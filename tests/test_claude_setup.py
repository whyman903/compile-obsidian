from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from compile.cli import main, install_claude_files
from compile.workspace import init_workspace


def _make_workspace(tmp_path: Path, name: str = "wiki") -> Path:
    ws = tmp_path / name
    init_workspace(ws, "Test Wiki", "A test workspace.")
    return ws


def test_fresh_install(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    result = install_claude_files(ws, home, force=False)

    assert len(result["installed"]) == 9
    assert result["skipped"] == []
    assert result["mispointed"] == []

    # Global commands rendered with wiki path
    for name in ("capture.md", "wiki-context.md", "wiki-query.md"):
        content = (home / ".claude" / "commands" / name).read_text()
        assert str(ws) in content
        assert "{{wiki_path}}" not in content
        assert "wiki-visualize" not in content

    wiki_query = (home / ".claude" / "commands" / "wiki-query.md").read_text()
    assert "markdown is the fallback" not in wiki_query  # query template should stay concise
    assert "--nodes-file" in wiki_query
    assert "--script-file" in wiki_query

    # Workspace files exist
    workspace_claude = (ws / "CLAUDE.md").read_text()
    assert "markdown is the fallback, not the default" in workspace_claude.lower()
    assert "compile render canvas" in workspace_claude
    assert "/visualize" not in workspace_claude
    assert (ws / ".claude" / "settings.local.json").exists()
    for name in ("context.md", "ingest.md", "lint.md", "query.md"):
        assert (ws / ".claude" / "commands" / name).exists()


def test_skip_existing_without_force(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)
    result = install_claude_files(ws, home, force=False)

    assert result["installed"] == []
    assert len(result["skipped"]) == 9
    assert result["mispointed"] == []


def test_force_overwrites(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)
    result = install_claude_files(ws, home, force=True)

    assert len(result["installed"]) == 9
    assert result["skipped"] == []
    assert result["mispointed"] == []


def test_mispointed_globals_detected(tmp_path: Path) -> None:
    ws_a = _make_workspace(tmp_path, "wiki-a")
    ws_b = _make_workspace(tmp_path, "wiki-b")
    home = tmp_path / "home"
    home.mkdir()

    # Install for workspace A
    install_claude_files(ws_a, home, force=False)

    # Install for workspace B without --force
    result = install_claude_files(ws_b, home, force=False)

    # Globals should be flagged as mispointed (they still point at ws_a)
    assert len(result["mispointed"]) == 3
    # Workspace-local files for B should still install
    local_installed = [f for f in result["installed"] if str(ws_b) in f]
    assert len(local_installed) == 6  # CLAUDE.md + settings + 4 commands


def test_mispointed_globals_detected_with_prefix_overlap(tmp_path: Path) -> None:
    """Regression: wiki-old vs wiki — the shorter path is a prefix of the longer."""
    ws_old = _make_workspace(tmp_path, "wiki-old")
    ws_new = _make_workspace(tmp_path, "wiki")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws_old, home, force=False)
    result = install_claude_files(ws_new, home, force=False)

    # "wiki" is a substring of "wiki-old", but globals should still be flagged
    assert len(result["mispointed"]) == 3


def test_mispointed_globals_fixed_with_force(tmp_path: Path) -> None:
    ws_a = _make_workspace(tmp_path, "wiki-a")
    ws_b = _make_workspace(tmp_path, "wiki-b")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws_a, home, force=False)
    result = install_claude_files(ws_b, home, force=True)

    assert result["mispointed"] == []
    # Globals now point at ws_b
    for name in ("capture.md", "wiki-context.md", "wiki-query.md"):
        content = (home / ".claude" / "commands" / name).read_text()
        assert str(ws_b) in content
        assert str(ws_a) not in content


def test_path_with_spaces_quoted_in_shell_commands(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path, "My Wiki")
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)

    for name in ("capture.md", "wiki-context.md", "wiki-query.md"):
        content = (home / ".claude" / "commands" / name).read_text()
        # Shell commands should have quoted paths
        assert f'cd "{ws}"' in content
        # The unquoted form should NOT appear in shell command lines
        for line in content.splitlines():
            if line.strip().startswith(("cd ", "`cd ")):
                assert f'cd "{ws}"' in line, f"Unquoted path in shell command: {line}"


def test_no_visualize_templates_installed(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    install_claude_files(ws, home, force=False)

    assert not (home / ".claude" / "commands" / "wiki-visualize.md").exists()
    assert not (ws / ".claude" / "commands" / "visualize.md").exists()


def test_ingest_template_artifact_before_refresh(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    install_claude_files(ws, home, force=False)
    ingest_content = (ws / ".claude" / "commands" / "ingest.md").read_text()
    artifact_pos = ingest_content.index("Consider one companion")
    # Use the last occurrence of refresh/health (the main workflow steps, not the batch note)
    refresh_pos = ingest_content.rindex("compile obsidian refresh")
    health_pos = ingest_content.rindex("compile health")
    assert artifact_pos < refresh_pos < health_pos


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
