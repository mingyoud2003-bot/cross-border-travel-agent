SUPPORTED_SCOPE = {
    "ba_silver": [
        "ba silver",
        "british airways silver",
        "英航银卡",
        "英国航空银卡",
    ],
    "oneworld": [
        "oneworld",
        "寰宇一家",
    ],
    "qatar_airways": [
        "qatar airways",
        "卡塔尔航空",
    ],
}


def detect_supported_anchors(
    query: str,
) -> list[str]:
    """
    Detect business entities that are
    explicitly supported by the current
    loyalty knowledge base.
    """

    normalized_query = query.casefold()

    matched = []

    for entity_id, aliases in (
        SUPPORTED_SCOPE.items()
    ):
        for alias in aliases:
            if (
                alias.casefold()
                in normalized_query
            ):
                matched.append(entity_id)
                break

    return matched


def check_loyalty_scope(
    query: str,
) -> dict:
    """
    Determine whether the current
    knowledge base is in scope for
    this query.

    This is intentionally conservative:
    no supported anchor -> abstain.
    """

    matched_anchors = (
        detect_supported_anchors(query)
    )

    if not matched_anchors:
        return {
            "supported": False,
            "matched_anchors": [],
            "reason": (
                "当前常旅客知识库未覆盖"
                "该航空公司、联盟或会员体系。"
            ),
        }

    return {
        "supported": True,
        "matched_anchors": (
            matched_anchors
        ),
        "reason": None,
    }