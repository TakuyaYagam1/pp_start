from pathlib import Path

from app.core.stopwords import (
    DEFAULT_STOPWORDS,
    check_stop_words,
    load_default_stop_words,
    load_stop_words_from_file,
)


def test_default_stop_words_are_loaded_from_packaged_files() -> None:
    stop_words = load_default_stop_words()

    assert len(stop_words) >= 50
    assert "казино" in stop_words
    assert "casino" in stop_words
    assert "viagra" in stop_words
    assert DEFAULT_STOPWORDS == stop_words


def test_check_stop_words_matches_case_insensitively() -> None:
    result = check_stop_words("Заходи в лучшее КАЗИНО сегодня")

    assert result.matched is True
    assert result.matched_term == "казино"


def test_check_stop_words_matches_phrase() -> None:
    result = check_stop_words("Новый заработок онлайн без опыта")

    assert result.matched is True
    assert result.matched_term == "заработок онлайн"


def test_check_stop_words_matches_english_phrase() -> None:
    result = check_stop_words("Claim your free money today")

    assert result.matched is True
    assert result.matched_term == "free money"


def test_check_stop_words_returns_negative_result_for_neutral_text() -> None:
    result = check_stop_words("Обычное сообщение про расписание встречи")

    assert result.matched is False
    assert result.matched_term is None


def test_check_stop_words_accepts_custom_stop_words() -> None:
    result = check_stop_words(
        "Нужна ручная проверка",
        stop_words=("ручная проверка",),
    )

    assert result.matched is True
    assert result.matched_term == "ручная проверка"


def test_load_stop_words_from_file_ignores_blank_comments_and_duplicates(
    tmp_path: Path,
) -> None:
    stopword_file = tmp_path / "custom_stopwords.txt"
    stopword_file.write_text(
        "\n# comment\ncasino\n Casino \nкрипта\n",
        encoding="utf-8",
    )

    assert load_stop_words_from_file(stopword_file) == ("casino", "крипта")
