"""BidNet Direct (Texas, location=277) — Jina + Perplexity."""
from perplexity_client import fetch_via_jina, analyze_with_perplexity

SOURCE_NAME = "BidNet"
LIST_URL = "https://www.bidnetdirect.com/public/solicitations/open?keywords=RFP&location=277"


def fetch() -> list[dict]:
    text = fetch_via_jina(LIST_URL, char_limit=7000)
    rfps = analyze_with_perplexity(
        text,
        context="Source: BidNet Direct (Texas open solicitations). Focus on lobbying, government relations, legislative consulting.",
    )
    for r in rfps:
        r["source"] = SOURCE_NAME
    return rfps
