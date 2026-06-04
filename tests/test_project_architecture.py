import ast
import asyncio
import io
import json
import re
import tokenize
import tomllib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.__main__ import build_parser
from app.bootstrap.application import (
    ALLOWED_UPDATES,
    create_application,
)
from app.bootstrap.command import BOT_COMMANDS, set_bot_commands
from app.bootstrap.verification_timer import (
    pending_verification_ttl_seconds,
    restore_pending_verification_timer,
)
from app.bot.controller.v1 import admin_router, user_router
from app.bot.controller.v1.moderation import router as moderation_router
from app.bot.controller.v1.verification import router as verification_router
from app.bot.middleware import RedisMiddleware
from app.bot.state import UserVerificationStates
from app.config import Settings
from app.domain import ActionMode, PendingVerification
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.redis import (
    PendingVerificationRepository,
    RuntimeSettingsRepository,
)
from app.observability.logging import configure_logging
from app.usecase.moderation import AutoDeleteTaskRegistry
from app.usecase.verification import VerificationTaskRegistry


def test_clean_architecture_import_paths_are_available() -> None:
    assert Settings.__name__ == "Settings"
    assert ActionMode.DELETE.value == "delete"
    assert PendingVerificationRepository.__name__ == "PendingVerificationRepository"
    assert RuntimeSettingsRepository.__name__ == "RuntimeSettingsRepository"
    assert LLMClient.__name__ == "LLMClient"
    assert VerificationTaskRegistry.__name__ == "VerificationTaskRegistry"
    assert configure_logging.__name__ == "configure_logging"
    assert admin_router.name == "admin"
    assert user_router.name == "user"
    assert moderation_router.name == "moderation"
    assert verification_router.name == "verification"
    assert RedisMiddleware.__name__ == "RedisMiddleware"
    assert UserVerificationStates.__name__ == "UserVerificationStates"


def test_package_entrypoint_parser_name_matches_python_module() -> None:
    assert build_parser().prog == "python -m app"
    assert "chat_join_request" in ALLOWED_UPDATES
    assert "chat_member" in ALLOWED_UPDATES


def test_project_files_match_clean_architecture() -> None:
    required_paths = [
        ".env.example",
        ".gitignore",
        ".dockerignore",
        "docker-compose.yml",
        "Dockerfile",
        "Makefile",
        "pyproject.toml",
        "requirements.lock",
        "scripts/make_help.py",
        "app/__main__.py",
        "app/bootstrap/application.py",
        "app/bootstrap/command.py",
        "app/bootstrap/lifecycle.py",
        "app/bootstrap/verification_restore.py",
        "app/bootstrap/verification_timer.py",
        "app/config/settings.py",
        "app/domain/moderation.py",
        "app/domain/spam.py",
        "app/domain/stopword.py",
        "app/domain/verification.py",
        "app/usecase/contract.py",
        "app/usecase/moderation/action.py",
        "app/usecase/moderation/auto_delete.py",
        "app/usecase/moderation/flood_action.py",
        "app/usecase/moderation/message.py",
        "app/usecase/moderation/notification.py",
        "app/usecase/moderation/spam_detector.py",
        "app/usecase/moderation/stop_word_action.py",
        "app/usecase/verification/message.py",
        "app/usecase/verification/approval.py",
        "app/usecase/verification/challenge.py",
        "app/usecase/verification/flow.py",
        "app/usecase/verification/permission.py",
        "app/usecase/verification/task.py",
        "app/usecase/verification/timeout.py",
        "app/infrastructure/redis/client.py",
        "app/infrastructure/redis/repository/pending_verification.py",
        "app/infrastructure/redis/repository/verified_user.py",
        "app/infrastructure/redis/repository/runtime_setting.py",
        "app/infrastructure/redis/repository/duplicate_message.py",
        "app/infrastructure/redis/repository/auto_delete_message.py",
        "app/infrastructure/redis/repository/llm_cache.py",
        "app/infrastructure/redis/repository/stop_word_warning.py",
        "app/infrastructure/llm/client.py",
        "app/infrastructure/llm/prompt.py",
        "app/domain/data/stopword/spam_ru.txt",
        "app/domain/data/stopword/spam_en.txt",
        "app/observability/logging.py",
        "app/bot/controller/__init__.py",
        "app/bot/controller/v1/__init__.py",
        "app/bot/controller/v1/admin/__init__.py",
        "app/bot/controller/v1/admin/argument.py",
        "app/bot/controller/v1/admin/callback.py",
        "app/bot/controller/v1/admin/command.py",
        "app/bot/controller/v1/admin/panel.py",
        "app/bot/controller/v1/admin/permission.py",
        "app/bot/controller/v1/admin/router.py",
        "app/bot/controller/v1/moderation/__init__.py",
        "app/bot/controller/v1/moderation/action.py",
        "app/bot/controller/v1/moderation/flood.py",
        "app/bot/controller/v1/moderation/message.py",
        "app/bot/controller/v1/moderation/router.py",
        "app/bot/controller/v1/user.py",
        "app/bot/controller/v1/verification.py",
        "app/bot/keyboard/reply.py",
        "app/bot/middleware/redis.py",
        "app/bot/state/user.py",
        "app/bot/util/text.py",
    ]

    assert [path for path in required_paths if not Path(path).is_file()] == []


def test_no_relational_database_scaffold_for_redis_only_v1() -> None:
    assert not Path("app/database").exists()
    assert not Path("migrations").exists()
    assert not Path("app/main.py").exists()
    assert not Path("app/logging.py").exists()
    assert not Path("app/tg_bot").exists()
    assert not Path("app/bot/handler").exists()
    assert not Path("app/cache").exists()
    assert not Path("app/core").exists()
    assert not Path("app/core/models.py").exists()
    assert not Path("app/core/services").exists()
    assert not Path("app/core/llm").exists()
    assert not Path("requirements.txt").exists()


def test_usecase_layer_does_not_import_infrastructure_adapters() -> None:
    violations = []
    for path in sorted(Path("app/usecase").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "app.infrastructure" in source:
            violations.append(path.as_posix())

    assert violations == []


def test_pyproject_declares_runtime_and_tooling() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.14"
    assert pyproject["build-system"]["requires"] == [
        "setuptools==82.0.1",
        "wheel==0.47.0",
    ]
    dependencies = set(pyproject["project"]["dependencies"])
    assert dependencies == {
        "aiogram==3.28.2",
        "aiohttp-socks==0.11.0",
        "httpx==0.28.1",
        "pydantic-settings==2.14.1",
        "redis==8.0.0",
    }
    assert pyproject["tool"]["setuptools"]["package-data"][
        "app.domain.data.stopword"
    ] == ["*.txt"]
    assert "dev" in pyproject["project"]["optional-dependencies"]
    assert set(pyproject["project"]["optional-dependencies"]["dev"]) == {
        "mypy==2.1.0",
        "pytest==9.0.3",
        "ruff==0.15.15",
        "testcontainers[redis]==4.14.2",
    }
    assert pyproject["tool"]["mypy"]["files"] == ["app"]
    assert pyproject["project"]["name"] == "TelegramSpamGuardBot"
    assert pyproject["project"]["scripts"]["telegram-spam-guard-bot"] == (
        "app.__main__:main"
    )


def test_compose_passes_all_env_example_settings_to_bot_container() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    env_keys = {
        line.split("=", maxsplit=1)[0]
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    compose_keys = set(re.findall(r"^\s{6}([A-Z0-9_]+):", compose, flags=re.M))

    assert sorted(env_keys - compose_keys) == []


def test_python_comments_and_docstrings_do_not_end_with_period() -> None:
    violations: list[str] = []
    python_files = sorted([*Path("app").rglob("*.py"), *Path("tests").rglob("*.py")])

    for path in python_files:
        source = path.read_text(encoding="utf-8")
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and token.string.rstrip().endswith("."):
                violations.append(f"{path.as_posix()}:{token.start[0]}")

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(
                node,
                ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            ):
                continue
            docstring = ast.get_docstring(node, clean=True)
            if docstring is not None and docstring.rstrip().endswith("."):
                violations.append(f"{path.as_posix()}:{node.body[0].lineno}")

    assert violations == []


def test_ci_always_runs_pytest_for_this_project() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Run quality checks" in workflow
    assert "make check-ci ENV_FILE=.env.example" in workflow
    assert "Skip pytest" not in workflow
    assert "Detect tests" not in workflow
    assert "steps.tests.outputs.found" not in workflow
    assert "pytest-results.xml" in workflow


def test_application_uses_telegram_proxy_session_when_configured() -> None:
    captured_bot_kwargs: dict[str, object] = {}

    class FakeBot:
        def __init__(self, **kwargs: object) -> None:
            captured_bot_kwargs.update(kwargs)
            self.session = object()

    class FakeRedis:
        pass

    settings = Settings(
        bot_token="token",
        telegram_proxy_url="socks5://xray:10808",
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        llm_api_key="llm-token",
        llm_base_url="https://api.example.com/v1",
        llm_model="model",
        llm_timeout_seconds=8,
        log_file="spam.log",
    )

    create_application(
        settings,
        bot_factory=FakeBot,
        redis_client=FakeRedis(),
    )

    assert captured_bot_kwargs["token"] == "token"
    assert captured_bot_kwargs["session"].proxy == "socks5://xray:10808"


def test_application_owns_runtime_task_registries() -> None:
    class FakeBot:
        def __init__(self, **kwargs: object) -> None:
            self.session = object()

    class FakeRedis:
        pass

    settings = Settings(
        bot_token="token",
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        llm_api_key="llm-token",
        llm_base_url="https://api.example.com/v1",
        llm_model="model",
        llm_timeout_seconds=8,
        log_file="spam.log",
    )

    first = create_application(
        settings,
        bot_factory=FakeBot,
        redis_client=FakeRedis(),
    )
    second = create_application(
        settings,
        bot_factory=FakeBot,
        redis_client=FakeRedis(),
    )

    assert isinstance(first.verification_task_registry, VerificationTaskRegistry)
    assert isinstance(first.auto_delete_task_registry, AutoDeleteTaskRegistry)
    assert first.verification_task_registry is not second.verification_task_registry
    assert first.auto_delete_task_registry is not second.auto_delete_task_registry
    assert first.dispatcher.workflow_data["verification_task_registry"] is (
        first.verification_task_registry
    )
    assert first.dispatcher.workflow_data["auto_delete_task_registry"] is (
        first.auto_delete_task_registry
    )


def test_pending_verification_redis_ttl_has_cleanup_grace() -> None:
    settings = Settings(
        bot_token="token",
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        llm_api_key="llm-token",
        llm_base_url="https://api.example.com/v1",
        llm_model="model",
        llm_timeout_seconds=8,
        log_file="spam.log",
    )

    assert pending_verification_ttl_seconds(settings) == 240


def test_restored_member_verification_timeout_uses_ban_unban_flow() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.expirations: dict[str, int] = {}

        async def scan_iter(self, match: str) -> object:
            prefix = match.removesuffix("*")
            for key in tuple(self.values):
                if key.startswith(prefix):
                    yield key

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def delete(self, key: str) -> int:
            existed = key in self.values
            self.values.pop(key, None)
            self.expirations.pop(key, None)
            return int(existed)

        async def ttl(self, key: str) -> int:
            if key not in self.values:
                return -2
            return self.expirations.get(key, -1)

    class FakeBot:
        def __init__(self) -> None:
            self.bans: list[dict[str, int]] = []
            self.unbans: list[dict[str, int]] = []
            self.declined_join_requests: list[dict[str, int]] = []
            self.deleted_messages: list[dict[str, int]] = []

        async def ban_chat_member(self, **kwargs: int) -> None:
            self.bans.append(kwargs)

        async def unban_chat_member(self, **kwargs: int) -> None:
            self.unbans.append(kwargs)

        async def decline_chat_join_request(self, **kwargs: int) -> None:
            self.declined_join_requests.append(kwargs)

        async def delete_message(self, **kwargs: int) -> None:
            self.deleted_messages.append(kwargs)

    async def run() -> None:
        redis = FakeRedis()
        bot = FakeBot()
        pending_repository = PendingVerificationRepository(redis, ttl_seconds=240)
        registries = VerificationTaskRegistry()
        settings = Settings(
            bot_token="token",
            redis_url="redis://redis:6379/0",
            verify_timeout_seconds=180,
            action_mode=ActionMode.NOTIFY_ADMIN,
            admin_username="@admin",
            llm_api_key="llm-token",
            llm_base_url="https://api.example.com/v1",
            llm_model="model",
            llm_timeout_seconds=8,
            log_file="spam.log",
        )
        pending = PendingVerification(
            user_id=42,
            chat_id=-100123,
            verification_message_id=777,
            created_at=(datetime.now(UTC) - timedelta(seconds=181)).isoformat(),
        )
        key = PendingVerificationRepository.key(-100123, 42)
        redis.values[key] = json.dumps(asdict(pending), ensure_ascii=False)
        redis.expirations[key] = 59

        restored = await restore_pending_verification_timer(
            redis_client=redis,
            bot=bot,
            pending_verification_repository=pending_repository,
            verification_task_registry=registries,
            settings=settings,
        )

        task = registries.timeout_task[(-100123, 42)]
        removed = await task

        assert restored == 1
        assert removed is True
        assert bot.bans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.unbans == [{"chat_id": -100123, "user_id": 42}]
        assert bot.declined_join_requests == []
        assert bot.deleted_messages == [{"chat_id": -100123, "message_id": 777}]
        assert await pending_repository.get(chat_id=-100123, user_id=42) is None

    asyncio.run(run())


def test_bot_commands_are_registered_for_telegram_menu() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def delete_my_commands(self, **kwargs: object) -> None:
            self.calls.append({"method": "delete_my_commands", **kwargs})

        async def set_my_commands(
            self, commands: list[object], **kwargs: object
        ) -> None:
            self.calls.append(
                {"method": "set_my_commands", "commands": commands, **kwargs}
            )

    bot = FakeBot()
    settings = Settings(
        bot_token="token",
        redis_url="redis://redis:6379/0",
        verify_timeout_seconds=180,
        action_mode=ActionMode.NOTIFY_ADMIN,
        admin_username="@admin",
        admin_id=42,
        llm_api_key="llm-token",
        llm_base_url="https://api.example.com/v1",
        llm_model="model",
        llm_timeout_seconds=8,
        log_file="spam.log",
    )

    asyncio.run(set_bot_commands(bot, settings))

    assert [command.command for command in BOT_COMMANDS] == [
        "admin",
        "help",
        "mode",
        "notify",
    ]
    assert [type(call["scope"]).__name__ for call in bot.calls] == [
        "BotCommandScopeDefault",
        "BotCommandScopeAllGroupChats",
        "BotCommandScopeAllPrivateChats",
        "BotCommandScopeAllChatAdministrators",
        "BotCommandScopeChat",
    ]
    assert [call["method"] for call in bot.calls] == [
        "delete_my_commands",
        "delete_my_commands",
        "delete_my_commands",
        "set_my_commands",
        "set_my_commands",
    ]
    assert bot.calls[3]["commands"] == list(BOT_COMMANDS)
    assert bot.calls[4]["commands"] == list(BOT_COMMANDS)
    assert bot.calls[4]["scope"].chat_id == 42
