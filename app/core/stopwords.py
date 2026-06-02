from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from importlib import resources
from pathlib import Path

from app.core.models import StopWordCheckResult


STOPWORD_RESOURCE_PACKAGE = "app.core.data.stopwords"
DEFAULT_STOPWORD_FILES: tuple[str, ...] = (
    "spam_ru.txt",
    "spam_en.txt",
)


def _parse_stopword_lines(text: str) -> tuple[str, ...]:
    stop_words: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = line.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        stop_words.append(line)
    return tuple(stop_words)


def load_stop_words_from_file(path: str | Path) -> tuple[str, ...]:
    return _parse_stopword_lines(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_default_stop_words() -> tuple[str, ...]:
    stop_words: list[str] = []
    for filename in DEFAULT_STOPWORD_FILES:
        resource = resources.files(STOPWORD_RESOURCE_PACKAGE).joinpath(filename)
        stop_words.extend(_parse_stopword_lines(resource.read_text(encoding="utf-8")))
    return tuple(dict.fromkeys(stop_words))


DEFAULT_STOPWORDS: tuple[str, ...] = load_default_stop_words()


def check_stop_words(
    text: str,
    stop_words: Iterable[str] | None = None,
) -> StopWordCheckResult:
    resolved_stop_words = DEFAULT_STOPWORDS if stop_words is None else stop_words
    normalized_text = text.casefold()
    for stop_word in resolved_stop_words:
        normalized_stop_word = stop_word.casefold()
        if normalized_stop_word in normalized_text:
            return StopWordCheckResult(matched=True, matched_term=stop_word)
    return StopWordCheckResult(matched=False)
