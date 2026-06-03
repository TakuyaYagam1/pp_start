from __future__ import annotations

import string
from collections.abc import Iterable
from typing import Protocol

from app.core import LLMDecision, SpamDetectionResult, StopWordCheckResult
from app.core.stopwords import DEFAULT_STOPWORDS, check_stop_words


AFFIRMATIVE_LLM_ANSWERS = {"да", "yes"}
NEGATIVE_LLM_ANSWERS = {"нет", "no"}


class LLMClientProtocol(Protocol):
    async def ask_is_spam(self, message_text: str) -> str: ...


class LLMResultCacheProtocol(Protocol):
    async def get(self, text: str) -> str | None: ...

    async def set(self, text: str, result: str) -> None: ...


def parse_llm_decision(answer: str) -> LLMDecision:
    normalized = answer.strip().casefold().strip(string.whitespace + string.punctuation)
    if normalized in AFFIRMATIVE_LLM_ANSWERS:
        return LLMDecision.SPAM
    if normalized in NEGATIVE_LLM_ANSWERS:
        return LLMDecision.NOT_SPAM
    return LLMDecision.UNKNOWN


def build_spam_detection_result(
    *,
    stop_word: StopWordCheckResult,
    llm_answer: str | None = None,
    llm_error: Exception | None = None,
) -> SpamDetectionResult:
    if llm_error is not None:
        return SpamDetectionResult(
            is_spam=stop_word.matched,
            reason="llm_error_fallback_to_stop_word",
            stop_word=stop_word,
            llm_decision=LLMDecision.UNKNOWN,
            matched_term=stop_word.matched_term,
        )

    if llm_answer is None:
        return SpamDetectionResult(
            is_spam=stop_word.matched,
            reason="llm_missing_fallback_to_stop_word",
            stop_word=stop_word,
            llm_decision=LLMDecision.UNKNOWN,
            matched_term=stop_word.matched_term,
        )

    llm_decision = parse_llm_decision(llm_answer)
    if llm_decision == LLMDecision.SPAM:
        return SpamDetectionResult(
            is_spam=True,
            reason="llm_spam",
            stop_word=stop_word,
            llm_decision=llm_decision,
            matched_term=stop_word.matched_term,
        )
    if llm_decision == LLMDecision.NOT_SPAM:
        return SpamDetectionResult(
            is_spam=False,
            reason="llm_not_spam",
            stop_word=stop_word,
            llm_decision=llm_decision,
            matched_term=stop_word.matched_term,
        )

    return SpamDetectionResult(
        is_spam=stop_word.matched,
        reason="llm_unknown_fallback_to_stop_word",
        stop_word=stop_word,
        llm_decision=llm_decision,
        matched_term=stop_word.matched_term,
    )


class SpamDetectorService:
    def __init__(
        self,
        *,
        llm_client: LLMClientProtocol | None = None,
        llm_cache_repository: LLMResultCacheProtocol | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._llm_cache_repository = llm_cache_repository

    def parse_llm_decision(self, answer: str) -> LLMDecision:
        return parse_llm_decision(answer)

    def build_result(
        self,
        *,
        stop_word: StopWordCheckResult,
        llm_answer: str | None = None,
        llm_error: Exception | None = None,
    ) -> SpamDetectionResult:
        return build_spam_detection_result(
            stop_word=stop_word,
            llm_answer=llm_answer,
            llm_error=llm_error,
        )

    async def ask_llm_with_cache(self, message_text: str) -> str:
        if self._llm_cache_repository is not None:
            cached_answer = await self._llm_cache_repository.get(message_text)
            if cached_answer is not None:
                return cached_answer

        if self._llm_client is None:
            raise ValueError("llm_client is required when LLM cache misses")

        answer = await self._llm_client.ask_is_spam(message_text)
        if self._llm_cache_repository is not None:
            await self._llm_cache_repository.set(message_text, answer)
        return answer

    async def detect(
        self,
        message_text: str,
        *,
        stop_words: Iterable[str] = DEFAULT_STOPWORDS,
    ) -> SpamDetectionResult:
        stop_word = check_stop_words(message_text, stop_words=stop_words)
        if not stop_word.matched:
            return SpamDetectionResult(
                is_spam=False,
                reason="no_stop_word",
                stop_word=stop_word,
                llm_decision=LLMDecision.UNKNOWN,
            )

        try:
            llm_answer = await self.ask_llm_with_cache(message_text)
        except Exception as exc:
            return build_spam_detection_result(stop_word=stop_word, llm_error=exc)

        return build_spam_detection_result(
            stop_word=stop_word,
            llm_answer=llm_answer,
        )

    async def detect_duplicate_flood(
        self,
        message_text: str,
        *,
        duplicate_count: int,
    ) -> SpamDetectionResult:
        llm_message = build_duplicate_flood_llm_message(
            message_text=message_text,
            duplicate_count=duplicate_count,
        )
        try:
            llm_answer = await self.ask_llm_with_cache(llm_message)
        except Exception:
            return SpamDetectionResult(
                is_spam=False,
                reason="llm_duplicate_flood_error",
                stop_word=StopWordCheckResult(matched=False),
                llm_decision=LLMDecision.UNKNOWN,
                matched_term="duplicate_message",
            )

        llm_decision = parse_llm_decision(llm_answer)
        if llm_decision == LLMDecision.SPAM:
            return SpamDetectionResult(
                is_spam=True,
                reason="llm_duplicate_flood_spam",
                stop_word=StopWordCheckResult(matched=False),
                llm_decision=llm_decision,
                matched_term="duplicate_message",
            )
        if llm_decision == LLMDecision.NOT_SPAM:
            return SpamDetectionResult(
                is_spam=False,
                reason="llm_duplicate_flood_not_spam",
                stop_word=StopWordCheckResult(matched=False),
                llm_decision=llm_decision,
                matched_term="duplicate_message",
            )

        return SpamDetectionResult(
            is_spam=False,
            reason="llm_duplicate_flood_unknown",
            stop_word=StopWordCheckResult(matched=False),
            llm_decision=llm_decision,
            matched_term="duplicate_message",
        )


def build_duplicate_flood_llm_message(
    *,
    message_text: str,
    duplicate_count: int,
) -> str:
    return (
        "Пользователь отправил одно и то же сообщение "
        f"{duplicate_count} раз подряд в групповом чате. "
        "Это flood/spam или подозрительное поведение? "
        f"Текст повторяющегося сообщения: {message_text}"
    )
