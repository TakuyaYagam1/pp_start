from __future__ import annotations

import asyncio

from pydantic import SecretStr

from app.bootstrap.lifecycle import on_shutdown, set_bot_commands_best_effort
from app.config import Settings
from app.domain import ActionMode


class FakeTaskRegistry:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel_all(self) -> None:
        self.cancelled = True


class FakeBotSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeBot:
    def __init__(self) -> None:
        self.session = FakeBotSession()


class FakeRedisClient:
    def __init__(self) -> None:
        self.closed = False
        self.close_connection_pool = False

    async def aclose(self, *, close_connection_pool: bool) -> None:
        self.closed = True
        self.close_connection_pool = close_connection_pool


class FakeLLMClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FailingLLMClient(FakeLLMClient):
    async def aclose(self) -> None:
        self.closed = True
        raise RuntimeError("llm close failed")


class FailingCommandBot:
    def __init__(self) -> None:
        self.delete_attempts = 0

    async def delete_my_commands(self, **kwargs: object) -> None:
        self.delete_attempts += 1
        raise RuntimeError("command registration failed")

    async def set_my_commands(self, commands: list[object], **kwargs: object) -> None:
        raise RuntimeError("command registration failed")


def _settings() -> Settings:
    return Settings(
        bot_token=SecretStr("123456:test-token"),
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        llm_api_key=SecretStr("llm-secret"),
        llm_base_url="https://llm.example/v1",
        llm_model="test-model",
        llm_timeout_seconds=5,
        log_level="INFO",
        log_file="spam.log",
    )


def test_bot_command_registration_failure_is_non_blocking() -> None:
    async def run() -> None:
        bot = FailingCommandBot()

        await set_bot_commands_best_effort(bot=bot, settings=_settings())

        assert bot.delete_attempts == 1

    asyncio.run(run())


def test_shutdown_closes_runtime_task_registries_llm_bot_and_redis() -> None:
    async def run() -> None:
        bot = FakeBot()
        redis_client = FakeRedisClient()
        verification_task_registry = FakeTaskRegistry()
        auto_delete_task_registry = FakeTaskRegistry()
        llm_client = FakeLLMClient()

        await on_shutdown(
            bot=bot,
            redis_client=redis_client,
            verification_task_registry=verification_task_registry,
            auto_delete_task_registry=auto_delete_task_registry,
            llm_client=llm_client,
        )

        assert verification_task_registry.cancelled is True
        assert auto_delete_task_registry.cancelled is True
        assert llm_client.closed is True
        assert bot.session.closed is True
        assert redis_client.closed is True
        assert redis_client.close_connection_pool is True

    asyncio.run(run())


def test_shutdown_continues_closing_resources_when_llm_close_fails() -> None:
    async def run() -> None:
        bot = FakeBot()
        redis_client = FakeRedisClient()
        verification_task_registry = FakeTaskRegistry()
        auto_delete_task_registry = FakeTaskRegistry()
        llm_client = FailingLLMClient()

        try:
            await on_shutdown(
                bot=bot,
                redis_client=redis_client,
                verification_task_registry=verification_task_registry,
                auto_delete_task_registry=auto_delete_task_registry,
                llm_client=llm_client,
            )
        except RuntimeError as exc:
            assert str(exc) == "shutdown cleanup failed"
            assert isinstance(exc.__cause__, RuntimeError)
            assert str(exc.__cause__) == "llm close failed"
        else:
            raise AssertionError("shutdown should report close failure")

        assert verification_task_registry.cancelled is True
        assert auto_delete_task_registry.cancelled is True
        assert llm_client.closed is True
        assert bot.session.closed is True
        assert redis_client.closed is True
        assert redis_client.close_connection_pool is True

    asyncio.run(run())
