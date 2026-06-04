"""CivCast USA — Jina + Perplexity."""
from perplexity_client import fetch_via_jina, analyze_with_perplexity

SOURCE_NAME = "CivCast"
LIST_URL = "https://www.civcastusa.com/bids?page=1&timeInfo=0&isReversed=true&orderBy=BidDate"


def fetch() -> list[dict]:
    text = fetch_via_jina(LIST_URL, char_limit=7000)
    rfps = analyze_with_perplexity(
        text,
        context="Source: CivCast USA. Extract Texas-based RFPs only. Focus on lobbying, government affairs, consulting.",
    )
    for r in rfps:
        r["source"] = SOURCE_NAME
    return rfps
