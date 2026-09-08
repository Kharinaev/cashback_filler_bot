import hashlib

from src.bot_helpers import normalize_text


EMOJI_RULES = (
    (("все покупки", "любые покупки"), "💳"),
    (("самокат",), "🛴"),
    (("такси",), "🚕"),
    (("транспорт", "метро", "автобус"), "🚌"),
    (("ж д", "железнодорож", "поезд"), "🚆"),
    (("авиа", "авиабилет"), "✈️"),
    (("отел", "гостиниц"), "🏨"),
    (("путешеств", "тревел"), "🧳"),
    (("топливо", "азс", "заправ"), "⛽"),
    (("авто", "машин"), "🚗"),
    (("супермаркет", "продукт", "пятероч", "перекресток", "лента"), "🛒"),
    (("вкусвилл",), "🥑"),
    (("ресторан", "кафе"), "🍽️"),
    (("фастфуд",), "🍔"),
    (("кофе",), "☕"),
    (("доставк",), "📦"),
    (("яндекс еда", "еда"), "🥡"),
    (("аптек",), "💊"),
    (("медицин", "здоров"), "🩺"),
    (("красот", "салон", "спа"), "💅"),
    (("фитнес",), "🏋️"),
    (("спорт", "активный отдых"), "⚽"),
    (("одежд", "обув"), "👗"),
    (("шопинг", "маркетплейс"), "🛍️"),
    (("цвет",), "💐"),
    (("образован", "курс"), "🎓"),
    (("книг",), "📚"),
    (("техник", "электроник"), "💻"),
    (("цифров",), "🎮"),
    (("фото", "видео"), "📷"),
    (("дом", "ремонт"), "🏠"),
    (("мебел",), "🛋️"),
    (("развлеч", "кино"), "🎬"),
    (("концерт",), "🎵"),
    (("выстав", "музе", "искусств"), "🎨"),
    (("сувенир", "подар"), "🎁"),
    (("премиум",), "👑"),
    (("связ", "мобил", "интернет"), "📱"),
    (("зоотовар", "питом"), "🐾"),
    (("страхован",), "🛡️"),
)

FALLBACK_EMOJIS = (
    "✨",
    "⭐",
    "🎯",
    "🧾",
    "🪙",
    "🏷️",
    "💰",
    "🎉",
    "🔖",
    "🧺",
)


def category_emoji(category):
    normalized = normalize_text(category)
    for needles, emoji in EMOJI_RULES:
        if any(needle in normalized for needle in needles):
            return emoji
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    return FALLBACK_EMOJIS[digest[0] % len(FALLBACK_EMOJIS)]


def normalize_category_emoji(value, category):
    parts = str(value or "").strip().split(maxsplit=1)
    candidate = parts[0] if parts else ""
    if (
        candidate
        and len(candidate) <= 8
        and not any(character.isalnum() for character in candidate)
    ):
        return candidate
    return category_emoji(category)
