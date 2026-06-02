import ast
import io
import tokenize
from pathlib import Path
import tomllib

from app.__main__ import ALLOWED_UPDATES, build_parser
from app.cache.redis import PendingVerificationRepository, RuntimeSettingsRepository
from app.config import Settings
from app.core.llm.client import LLMClient
from app.core.models import ActionMode
from app.tg_bot.handlers import admin_router, user_router
from app.tg_bot.handlers.moderation import router as moderation_router
from app.tg_bot.handlers.verification import router as verification_router
from app.tg_bot.middlewares import RedisMiddleware
from app.tg_bot.states import UserVerificationStates


def test_clean_architecture_import_paths_are_available() -> None:
    assert Settings.__name__ == "Settings"
    assert ActionMode.DELETE.value == "delete"
    assert PendingVerificationRepository.__name__ == "PendingVerificationRepository"
    assert RuntimeSettingsRepository.__name__ == "RuntimeSettingsRepository"
    assert LLMClient.__name__ == "LLMClient"
    assert admin_router.name == "admin"
    assert user_router.name == "user"
    assert moderation_router.name == "moderation"
    assert verification_router.name == "verification"
    assert RedisMiddleware.__name__ == "RedisMiddleware"
    assert UserVerificationStates.__name__ == "UserVerificationStates"


def test_package_entrypoint_parser_name_matches_python_module() -> None:
    assert build_parser().prog == "python -m app"
    assert "chat_join_request" in ALLOWED_UPDATES
    assert "chat_member" not in ALLOWED_UPDATES


def test_project_files_match_clean_architecture() -> None:
    required_paths = [
        ".env.example",
        ".gitignore",
        ".dockerignore",
        "docker-compose.yml",
        "Dockerfile",
        "pyproject.toml",
        "app/__main__.py",
        "app/config/settings.py",
        "app/core/llm/client.py",
        "app/core/llm/prompts.py",
        "app/core/data/stopwords/spam_ru.txt",
        "app/core/data/stopwords/spam_en.txt",
        "app/cache/redis.py",
        "app/tg_bot/handlers/admin.py",
        "app/tg_bot/handlers/user.py",
        "app/tg_bot/keyboards/reply.py",
        "app/tg_bot/middlewares/redis.py",
        "app/tg_bot/states/user.py",
        "app/tg_bot/utils/texts.py",
    ]

    assert [path for path in required_paths if not Path(path).is_file()] == []


def test_no_relational_database_scaffold_for_redis_only_v1() -> None:
    assert not Path("app/database").exists()
    assert not Path("migrations").exists()
    assert not Path("app/main.py").exists()
    assert not Path("requirements.txt").exists()


def test_pyproject_declares_runtime_and_tooling() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["requires-python"] == ">=3.14"
    dependencies = set(pyproject["project"]["dependencies"])
    assert dependencies == {
        "aiogram>=3,<4",
        "httpx>=0.28,<1",
        "pydantic-settings>=2,<3",
        "redis>=5,<7",
    }
    assert pyproject["tool"]["setuptools"]["package-data"][
        "app.core.data.stopwords"
    ] == ["*.txt"]
    assert "dev" in pyproject["project"]["optional-dependencies"]
    assert pyproject["project"]["scripts"]["anti-spam-telegram-bot"] == (
        "app.__main__:main"
    )


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

    assert "Run pytest" in workflow
    assert "python -m pytest -ra --junitxml=pytest-results.xml" in workflow
    assert "Skip pytest" not in workflow
    assert "Detect tests" not in workflow
    assert "steps.tests.outputs.found" not in workflow
