from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalCity:
    city_id: str
    display_name: str
    timezone: str
    aliases: tuple[str, ...]


_CITIES = (
    CanonicalCity("DEBER", "柏林", "Europe/Berlin", ("Berlin", "柏林")),
    CanonicalCity("DECGN", "科隆", "Europe/Berlin", ("Cologne", "Köln", "Koeln", "科隆")),
    CanonicalCity("DEFRA", "法兰克福", "Europe/Berlin", ("Frankfurt", "Frankfurt am Main", "Frankfurt-am-Main", "法兰克福")),
    CanonicalCity("DEMUC", "慕尼黑", "Europe/Berlin", ("Munich", "München", "慕尼黑")),
    CanonicalCity("FRPAR", "巴黎", "Europe/Paris", ("Paris", "巴黎")),
    CanonicalCity("GBLON", "伦敦", "Europe/London", ("London", "伦敦")),
    CanonicalCity("ATVIE", "维也纳", "Europe/Vienna", ("Vienna", "Wien", "维也纳")),
    CanonicalCity("CHZRH", "苏黎世", "Europe/Zurich", ("Zurich", "Zürich", "苏黎世")),
    CanonicalCity("ITROM", "罗马", "Europe/Rome", ("Rome", "Roma", "罗马")),
    CanonicalCity("ESMAD", "马德里", "Europe/Madrid", ("Madrid", "马德里")),
    CanonicalCity("ESBCN", "巴塞罗那", "Europe/Madrid", ("Barcelona", "巴塞罗那")),
    CanonicalCity("NLAMS", "阿姆斯特丹", "Europe/Amsterdam", ("Amsterdam", "阿姆斯特丹")),
    CanonicalCity("BEBRU", "布鲁塞尔", "Europe/Brussels", ("Brussels", "Bruxelles", "Brussel", "布鲁塞尔")),
    CanonicalCity("MCMON", "摩纳哥", "Europe/Monaco", ("Monaco", "Monte Carlo", "摩纳哥", "蒙特卡洛")),
    CanonicalCity("CNBJS", "北京", "Asia/Shanghai", ("Beijing", "北京")),
    CanonicalCity("CNSHA", "上海", "Asia/Shanghai", ("Shanghai", "上海")),
    CanonicalCity("CNWUH", "武汉", "Asia/Shanghai", ("Wuhan", "武汉")),
    CanonicalCity("CNZMD", "驻马店", "Asia/Shanghai", ("Zhumadian", "驻马店")),
    CanonicalCity("CNTNA", "济南", "Asia/Shanghai", ("Jinan", "Tsinan", "济南")),
    CanonicalCity("CNSZX", "深圳", "Asia/Shanghai", ("Shenzhen", "深圳")),
    CanonicalCity(
        "HKHKG", "香港", "Asia/Hong_Kong", ("Hong Kong", "Hongkong", "香港")
    ),
    CanonicalCity("JPTYO", "东京", "Asia/Tokyo", ("Tokyo", "东京")),
    CanonicalCity("JPOSA", "大阪", "Asia/Tokyo", ("Osaka", "大阪")),
    CanonicalCity("SGSIN", "新加坡", "Asia/Singapore", ("Singapore", "新加坡")),
    CanonicalCity("USNYC", "纽约", "America/New_York", ("New York", "New York City", "NYC", "纽约")),
    CanonicalCity(
        "USLAX",
        "洛杉矶",
        "America/Los_Angeles",
        ("Los Angeles", "L.A.", "洛杉矶"),
    ),
    CanonicalCity(
        "USSFO",
        "旧金山",
        "America/Los_Angeles",
        ("San Francisco", "San Francisco Bay Area", "旧金山", "三藩市"),
    ),
)


def normalize_city_alias(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value.casefold())


_BY_ALIAS = {
    normalize_city_alias(alias): city
    for city in _CITIES
    for alias in (*city.aliases, city.display_name)
}


def resolve_city(value: str | None, timezone: str | None = None) -> CanonicalCity | None:
    if not value:
        return None
    city = _BY_ALIAS.get(normalize_city_alias(value))
    if city is None:
        return None
    if timezone and timezone != city.timezone:
        return None
    return city


def timezone_for_city(value: str | None) -> str | None:
    city = resolve_city(value)
    return city.timezone if city else None


def grounded_city(text: str, proposed: str | None) -> str | None:
    """Return a canonical label only when the user's text names that city."""

    if not proposed:
        return None
    city = resolve_city(proposed)
    if city:
        if any(_alias_in_text(text, alias) for alias in city.aliases):
            return city.display_name
        return None
    return proposed if _alias_in_text(text, proposed) else None


def grounded_text_value(text: str, proposed: str | None) -> str | None:
    """Keep a model-proposed free-text value only when it occurs in evidence."""

    if not proposed:
        return None
    return proposed if _alias_in_text(text, proposed) else None


def _alias_in_text(text: str, alias: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", alias):
        return alias.casefold() in text.casefold()
    return re.search(
        rf"(?<![\w]){re.escape(alias)}(?![\w])",
        text,
        flags=re.IGNORECASE,
    ) is not None
