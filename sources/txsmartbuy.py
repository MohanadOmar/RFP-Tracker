"""TX SmartBuy / ESBD — uses Jina + Perplexity (the site is JS-rendered)."""
from perplexity_client import fetch_via_jina, analyze_with_perplexity

SOURCE_NAME = "TX SmartBuy"
LIST_URL = "https://www.txsmartbuy.gov/esbd?status=1&page=1&dateRange=thisWeek"


def fetch() -> list[dict]:
    text = fetch_via_jina(LIST_URL, char_limit=7000)
    rfps = analyze_with_perplexity(
        text,
        context="Source: Texas SmartBuy ESBD. Extract all open Texas government RFPs.",
    )
    for r in rfps:
        r["source"] = SOURCE_NAME
        if not r.get("url"):
            r["url"] = f"https://www.txsmartbuy.gov/esbd/{r.get('solicitation_id', '')}"
    return rfps
