from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.core.models import LLMDecision, StopWordCheckResult
from app.core.services.spam_detector import SpamDetectorService, parse_llm_decision


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("да", LLMDecision.SPAM),
        ("Да.", LLMDecision.SPAM),
        (" yes! ", LLMDecision.SPAM),
        ("нет", LLMDecision.NOT_SPAM),
        ("Нет.", LLMDecision.NOT_SPAM),
        (" no ", LLMDecision.NOT_SPAM),
        ("maybe", LLMDecision.UNKNOWN),
        ("", LLMDecision.UNKNOWN),
    ],
)
def test_parse_llm_decision(answer: str, expected: LLMDecision) -> None:
    assert parse_llm_decision(answer) == expected


def test_build_result_falls_back_to_stop_word_on_unknown_answer() -> None:
    stop_word = StopWordCheckResult(matched=True, matched_term="казино")
    result = SpamDetectorService().build_result(
        stop_word=stop_word,
        llm_answer="не уверен",
    )

    assert result.is_spam is True
    assert result.reason == "llm_unknown_fallback_to_stop_word"
    assert result.llm_decision == LLMDecision.UNKNOWN
    assert result.matched_term == "казино"


@dataclass
class FakeLLMClient:
    answers: list[str] = field(default_factory=list)
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def ask_is_spam(self, message_text: str) -> str:
        self.calls.append(message_text)
        if self.error is not None:
            raise self.error
        if not self.answers:
            raise AssertionError("No fake LLM answer configured")
        return self.answers.pop(0)


@dataclass
class FakeLLMCacheRepository:
    cached_answer: str | None = None
    stored: list[tuple[str, str]] = field(default_factory=list)
    get_calls: list[str] = field(default_factory=list)

    async def get(self, text: str) -> str | None:
        self.get_calls.append(text)
        return self.cached_answer

    async def set(self, text: str, result: str) -> None:
        self.stored.append((text, result))


def test_detect_ignores_neutral_text_without_llm_call() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["да"])
        result = await SpamDetectorService(llm_client=llm_client).detect(
            "Обычное сообщение",
            stop_words=("казино",),
        )

        assert result.is_spam is False
        assert result.reason == "no_stop_word"
        assert llm_client.calls == []

    asyncio.run(run())


def test_detect_uses_llm_answer_for_stop_word_message() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["нет"])
        result = await SpamDetectorService(llm_client=llm_client).detect(
            "КАЗИНО прямо сейчас",
            stop_words=("казино",),
        )

        assert result.is_spam is False
        assert result.reason == "llm_not_spam"
        assert result.llm_decision == LLMDecision.NOT_SPAM
        assert llm_client.calls == ["КАЗИНО прямо сейчас"]

    asyncio.run(run())


def test_detect_falls_back_to_stop_word_on_llm_timeout() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(error=TimeoutError("llm timeout"))
        result = await SpamDetectorService(llm_client=llm_client).detect(
            "казино прямо сейчас",
            stop_words=("казино",),
        )

        assert result.is_spam is True
        assert result.reason == "llm_error_fallback_to_stop_word"
        assert result.llm_decision == LLMDecision.UNKNOWN
        assert result.matched_term == "казино"

    asyncio.run(run())


def test_ask_llm_with_cache_uses_cached_answer_without_llm_call() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["да"])
        cache = FakeLLMCacheRepository(cached_answer="нет")
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "нет"
        assert cache.get_calls == ["казино"]
        assert cache.stored == []
        assert llm_client.calls == []

    asyncio.run(run())


def test_ask_llm_with_cache_stores_cache_miss_answer() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["да"])
        cache = FakeLLMCacheRepository()
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "да"
        assert cache.get_calls == ["казино"]
        assert cache.stored == [("казино", "да")]
        assert llm_client.calls == ["казино"]

    asyncio.run(run())
