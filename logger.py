import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("spam.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("spam_bot")


def log_verify(user_id, username, chat_id, result):
    logger.info(
        f"ВЕРИФИКАЦИЯ | user={user_id} | @{username} | chat={chat_id} | {result}"
    )


def log_spam(user_id, username, text, matched_word, llm_result=None):
    preview = text[:100]
    llm_info = f" | llm={llm_result}" if llm_result else ""
    logger.info(
        f"СПАМ | user={user_id} | @{username} | слово='{matched_word}' | текст='{preview}'{llm_info}"
    )


def log_action(user_id, username, chat_id, action, details=""):
    logger.info(
        f"ДЕЙСТВИЕ | user={user_id} | @{username} | chat={chat_id} | {action} | {details}"
    )
