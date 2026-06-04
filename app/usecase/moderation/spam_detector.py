"""Spam detection usecase that combines stop-words, cache and LLM checks"""

from __future__ import annotations

import string
from collections.abc import Iterable

from app.domain import LLMDecision, SpamDetectionResult, StopWordCheckResult
from app.domain.stopword import DEFAULT_STOPWORDS, check_stop_words
from app.usecase.contract import LLMResultCache, LLMSpamClient

AFFIRMATIVE_LLM_ANSWERS = {"yes", "да"}
NEGATIVE_LLM_ANSWERS = {"no", "нет"}
LLM_TOKEN_STRIP_CHARS = string.whitespace + string.punctuation + "«»“”„…"


def parse_llm_decision(answer: str) -> LLMDecision:
    normalized = answer.strip().casefold().strip(LLM_TOKEN_STRIP_CHARS)
    first_token = ""
    if normalized:
        first_token = normalized.split(maxsplit=1)[0].strip(LLM_TOKEN_STRIP_CHARS)

    if normalized in AFFIRMATIVE_LLM_ANSWERS or first_token in AFFIRMATIVE_LLM_ANSWERS:
        return LLMDecision.SPAM
    if normalized in NEGATIVE_LLM_ANSWERS or first_token in NEGATIVE_LLM_ANSWERS:
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
        llm_client: LLMSpamClient | None = None,
        llm_cache_repository: LLMResultCache | None = None,
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
            cached_answer = await get_cached_llm_answer(
                self._llm_cache_repository,
                message_text,
            )
            if cached_answer is not None:
                return cached_answer

        if self._llm_client is None:
            raise ValueError("llm_client is required when LLM cache misses")

        answer = await self._llm_client.ask_is_spam(message_text)
        if self._llm_cache_repository is not None:
            await set_cached_llm_answer(
                self._llm_cache_repository,
                message_text,
                answer,
            )
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


async def get_cached_llm_answer(
    cache_repository: LLMResultCache,
    message_text: str,
) -> str | None:
    try:
        return await cache_repository.get(message_text)
    except Exception:
        return None


async def set_cached_llm_answer(
    cache_repository: LLMResultCache,
    message_text: str,
    answer: str,
) -> None:
    try:
        await cache_repository.set(message_text, answer)
    except Exception:
        return
