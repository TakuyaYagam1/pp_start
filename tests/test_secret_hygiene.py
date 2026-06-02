from __future__ import annotations

import re
import subprocess
from pathlib import Path


TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")
OPENROUTER_KEY_RE = re.compile(r"\b" + "sk" + r"-or-v1-[A-Za-z0-9_-]{20,}\b")


def _repository_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        Path(line)
        for line in result.stdout.splitlines()
        if line and Path(line).is_file()
    ]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_env_files_are_ignored_except_example() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")

    assert re.search(r"(?m)^\.env$", gitignore)
    assert re.search(r"(?m)^\.env\.\*$", gitignore)
    assert re.search(r"(?m)^!\.env\.example$", gitignore)
    assert Path(".env") not in _repository_files()


def test_env_example_contains_only_placeholders() -> None:
    env_example = Path(".env.example")
    text = _read_text(env_example)

    assert "BOT_TOKEN=replace_me" in text
    assert "LLM_API_KEY=replace_me" in text
    assert TELEGRAM_BOT_TOKEN_RE.search(text) is None
    assert OPENROUTER_KEY_RE.search(text) is None
    assert "Bearer " not in text


def test_repository_files_do_not_contain_common_runtime_secrets() -> None:
    leaks: list[str] = []
    for path in _repository_files():
        text = _read_text(path)
        if TELEGRAM_BOT_TOKEN_RE.search(text) or OPENROUTER_KEY_RE.search(text):
            leaks.append(path.as_posix())

    assert leaks == []


def test_readme_warns_not_to_commit_secrets() -> None:
    readme = _read_text(Path("README.md")).casefold()

    assert ".env" in readme
    assert "не коммит" in readme
    assert "секрет" in readme
