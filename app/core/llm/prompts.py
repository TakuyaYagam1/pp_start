def build_spam_detection_prompt(message_text: str) -> str:
    return (
        "Является ли следующее сообщение спамом или вредоносным? "
        'Ответь только "да" или "нет". '
        f"Сообщение: {message_text}"
    )
