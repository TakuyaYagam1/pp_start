STOP_WORDS = [
    "казино",
    "заработок онлайн",
    "биткоин",
    "бесплатно",
    "промокод",
    "скидки 90%",
    "переходи по ссылке",
    "инвестиции",
    "крипта",
    "займы",
    "пассивный доход",
    "лотерея",
    "выигрыш",
    "ставки на спорт",
]


def check_stopwords(text: str) -> tuple[bool, str | None]:
    text_lower = text.lower()
    for word in STOP_WORDS:
        if word in text_lower:
            return True, word
    return False, None
