"""One-time backfill: assign category to existing RFPs that don't have one.

Triggered via POST /backfill-categories on the Flask app. Uses a cheap
prompt that only outputs the category — no re-scoring, no re-extracting.
"""
import json
import re
import time
import requests
import os
import base44_client

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

CATEGORY_PROMPT = """You categorize Texas government RFPs for EMC Strategy Group.

Pick exactly ONE category from this list:
- Lobbying
- Grants
- Government Relations
- Web Development
- AI
- Other

Rules:
- "Lobbying" = lobbying services, legislative advocacy, political consulting
- "Grants" = grant writing, grant consulting, grant management
- "Government Relations" = public affairs, intergovernmental relations, policy
- "Web Development" = websites, web apps, portals
- "AI" = AI integration, machine learning, automation, chatbots
- "Other" = everything else

Return ONLY the category name. No punctuation, no explanation.
"""

VALID_CATEGORIES = {
    "Lobbying", "Grants", "Government Relations",
    "Web Development", "AI", "Other",
}


def _classify_one(title: str, description: str, agency: str) -> str:
    """Call Perplexity with a minimal prompt to get a single category back."""
    user = (
        f"Title: {title}\n"
        f"Agency: {agency}\n"
        f"Description: {description[:500]}"
    )
    try:
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": CATEGORY_PROMPT},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
            timeout=30,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"].strip()
        cleaned = re.sub(r"[^\w\s]", "", raw).strip()
        for cat in VALID_CATEGORIES:
            if cat.lower() == cleaned.lower():
                return cat
            if cat.lower() in cleaned.lower():
                return cat
        return "Other"
    except Exception as e:
        print(f"[backfill] Classify failed: {e}")
        return "Other"


def run_backfill(limit: int | None = None) -> dict:
    """Find RFPs without a category and classify them."""
    t0 = time.time()
    print("[backfill] Fetching all RFPs from Base44...")
    rfps = base44_client.list_rfps()
    print(f"[backfill] Total RFPs: {len(rfps)}")

    todo = [r for r in rfps if not r.get("category")]
    print(f"[backfill] Need category: {len(todo)}")

    if limit:
        todo = todo[:limit]
        print(f"[backfill] Limited to {limit} this run")

    classified = 0
    errors = []
    distribution = {}

    for rfp in todo:
        rfp_id = rfp.get("id") or rfp.get("_id")
        if not rfp_id:
            errors.append(f"RFP missing id: {rfp.get('solicitation_id', '?')}")
            continue

        category = _classify_one(
            title=rfp.get("title", ""),
            description=rfp.get("description", "") or rfp.get("ai_analysis", ""),
            agency=rfp.get("agency", ""),
        )

        try:
            base44_client.update_rfp(rfp_id, {"category": category})
            classified += 1
            distribution[category] = distribution.get(category, 0) + 1
            print(f"  {rfp.get('solicitation_id', '?')}: {category}")
        except Exception as e:
            errors.append(f"{rfp.get('solicitation_id', '?')}: {e}")
            print(f"  Update failed: {e}")

    duration = int(time.time() - t0)
    print(f"[backfill] Done in {duration}s — {classified} classified")
    print(f"[backfill] Distribution: {distribution}")

    return {
        "total_rfps": len(rfps),
        "needed_category": len(todo),
        "classified": classified,
        "distribution": distribution,
        "errors": errors,
        "duration_seconds": duration,
    }
