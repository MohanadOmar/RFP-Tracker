"""SAM.gov — uses their free public API (no scraping needed)."""
import os
import requests
from datetime import datetime, timedelta

SOURCE_NAME = "SAM.gov"
SAM_API = "https://api.sam.gov/opportunities/v2/search"
LOBBYING_KEYWORDS = ["lobbying", "government relations", "legislative", "advocacy"]


def fetch() -> list[dict]:
    api_key = os.environ.get("SAM_API_KEY", "DEMO_KEY")
    posted_from = (datetime.now() - timedelta(days=7)).strftime("%m/%d/%Y")
    posted_to = datetime.now().strftime("%m/%d/%Y")

    rfps = []
    for keyword in LOBBYING_KEYWORDS[:2]:
        try:
            params = {
                "api_key": api_key,
                "limit": 25,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "state": "TX",
                "q": keyword,
            }
            r = requests.get(SAM_API, params=params, timeout=30)
            if r.status_code != 200:
                continue
            data = r.json()
            for item in data.get("opportunitiesData", []):
                rfps.append(_normalize(item, keyword))
        except Exception as e:
            print(f"[SAM.gov] keyword '{keyword}' failed: {e}")
            continue

    seen = set()
    unique = []
    for r in rfps:
        sid = r.get("solicitation_id")
        if sid and sid not in seen:
            seen.add(sid)
            unique.append(r)
    return unique


def _normalize(item: dict, keyword: str) -> dict:
    notice_id = item.get("noticeId", "")
    deadline = item.get("responseDeadLine", "")[:10] if item.get("responseDeadLine") else None
    score = _score_relevance(item.get("title", ""), keyword)

    return {
        "solicitation_id": notice_id,
        "title": item.get("title", ""),
        "agency": item.get("organizationName", ""),
        "description": item.get("description", "")[:500] or "",
        "contact_info": _extract_contact(item),
        "deadline": deadline,
        "prebid_date": None,
        "url": f"https://sam.gov/opp/{notice_id}/view",
        "source": SOURCE_NAME,
        "ai_analysis": f"Matched keyword: {keyword}",
        "relevance_score": score,
        "relevance_reason": f"SAM.gov listing matching '{keyword}' in Texas",
    }


def _extract_contact(item: dict) -> str:
    contacts = item.get("pointOfContact", [])
    if not contacts:
        return ""
    c = contacts[0]
    return f"{c.get('fullName', '')} - {c.get('email', '')}".strip(" -")


def _score_relevance(title: str, keyword: str) -> int:
    t = title.lower()
    if "lobby" in t or "legislative" in t:
        return 9
    if "government relations" in t or "advocacy" in t:
        return 8
    if "public affairs" in t or "policy" in t:
        return 7
    return 5
