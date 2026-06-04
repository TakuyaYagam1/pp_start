from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from app.domain import LLMDecision, StopWordCheckResult
from app.usecase.moderation.spam_detector import (
    SpamDetectorService,
    parse_llm_decision,
)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (" yes! ", LLMDecision.SPAM),
        (" no ", LLMDecision.NOT_SPAM),
        ("Yes, this is spam", LLMDecision.SPAM),
        ("No, this is not spam", LLMDecision.NOT_SPAM),
        ("да", LLMDecision.SPAM),
        ("нет, это не спам", LLMDecision.NOT_SPAM),
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
    get_error: Exception | None = None
    set_error: Exception | None = None
    stored: list[tuple[str, str]] = field(default_factory=list)
    get_calls: list[str] = field(default_factory=list)

    async def get(self, text: str) -> str | None:
        self.get_calls.append(text)
        if self.get_error is not None:
            raise self.get_error
        return self.cached_answer

    async def set(self, text: str, result: str) -> None:
        if self.set_error is not None:
            raise self.set_error
        self.stored.append((text, result))


def test_detect_ignores_neutral_text_without_llm_call() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["yes"])
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
        llm_client = FakeLLMClient(answers=["no"])
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
        llm_client = FakeLLMClient(answers=["yes"])
        cache = FakeLLMCacheRepository(cached_answer="no")
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "no"
        assert cache.get_calls == ["казино"]
        assert cache.stored == []
        assert llm_client.calls == []

    asyncio.run(run())


def test_ask_llm_with_cache_stores_cache_miss_answer() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["yes"])
        cache = FakeLLMCacheRepository()
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "yes"
        assert cache.get_calls == ["казино"]
        assert cache.stored == [("казино", "yes")]
        assert llm_client.calls == ["казино"]

    asyncio.run(run())


def test_ask_llm_with_cache_uses_llm_when_cache_read_fails() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["no"])
        cache = FakeLLMCacheRepository(get_error=RuntimeError("redis failed"))
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "no"
        assert cache.get_calls == ["казино"]
        assert cache.stored == [("казино", "no")]
        assert llm_client.calls == ["казино"]

    asyncio.run(run())


def test_ask_llm_with_cache_returns_llm_answer_when_cache_write_fails() -> None:
    async def run() -> None:
        llm_client = FakeLLMClient(answers=["no"])
        cache = FakeLLMCacheRepository(set_error=RuntimeError("redis failed"))
        answer = await SpamDetectorService(
            llm_client=llm_client,
            llm_cache_repository=cache,
        ).ask_llm_with_cache("казино")

        assert answer == "no"
        assert cache.get_calls == ["казино"]
        assert cache.stored == []
        assert llm_client.calls == ["казино"]

    asyncio.run(run())
