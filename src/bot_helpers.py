import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher


def normalize_text(value):
    text = str(value or "").lower().replace("ё", "е")
    return " ".join(re.findall(r"[a-zа-я0-9]+", text))


def category_match_score(query, category):
    query = normalize_text(query)
    category = normalize_text(category)
    if not query or not category:
        return 0.0
    if query == category:
        return 1.0
    if query in category or category in query:
        return 0.9
    query_words = set(query.split())
    category_words = set(category.split())
    overlap = len(query_words & category_words) / max(
        len(query_words), len(category_words)
    )
    similarity = SequenceMatcher(None, query, category).ratio()
    return max(similarity, overlap)


def search_category_rows(rows, query, limit=10, threshold=0.55):
    matches = []
    for row in rows:
        score = category_match_score(query, row.get("Category"))
        if score >= threshold:
            matches.append((score, row))
    matches.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("Person", "")),
            str(item[1].get("Bank", "")),
        )
    )
    return [row for _, row in matches[:limit]]


def frequent_values(rows, field, limit=8, defaults=(), **filters):
    filtered = [
        row
        for row in rows
        if all(row.get(key) == value for key, value in filters.items())
    ]
    counts = Counter(
        str(row.get(field, "")).strip()
        for row in filtered
        if str(row.get(field, "")).strip()
    )
    result = [value for value, _ in counts.most_common(limit)]
    for value in defaults:
        normalized = str(value)
        if normalized not in result:
            result.append(normalized)
    return result[:limit]


def parse_percent(value):
    normalized = str(value).strip().replace(",", ".").rstrip("%").strip()
    percent = float(normalized)
    if percent < 0 or percent > 100:
        raise ValueError("Percent must be between 0 and 100")
    return int(percent) if percent.is_integer() else percent


def month_start(offset=0, now=None):
    current = now or datetime.now()
    month_index = current.year * 12 + current.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return f"{year:04d}-{zero_based_month + 1:02d}-01"
