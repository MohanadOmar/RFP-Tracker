"""Perplexity client — used by all sources for RFP extraction + scoring."""
import os
import json
import re
import requests
from urllib.parse import quote

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
JINA_BASE = os.environ.get("JINA_BASE_URL", "https://r.jina.ai/").rstrip("/")

SYSTEM_PROMPT = """You are an RFP analyst for EMC Strategy Group, a Texas-based firm.

EMC's services:
- Lobbying and legislative advocacy
- Grant writing and grant consulting
- Government relations and public affairs
- Web development and website design
- AI integrations and machine learning consulting

Extract every RFP/solicitation from the provided text. Return a JSON ARRAY only —
no markdown, no preamble. Each item must have these exact keys:

{
  "solicitation_id": "string (the unique ID/number)",
  "title": "string",
  "description": "string (1-3 sentence summary)",
  "agency": "string (issuing organization)",
  "contact_info": "string (email/phone if found, else empty)",
  "deadline": "YYYY-MM-DD or null",
  "prebid_date": "YYYY-MM-DD or null",
  "url": "string (direct link if found, else empty)",
  "requirements": ["string", "string"],
  "relevance_score": <integer 1-10>,
  "relevance_reason": "string explaining the score"
}

SCORING GUIDE (be discriminating, not generous):
- 9-10: Direct match — RFP explicitly asks for lobbying, government relations,
  legislative advocacy, grants consulting, web development, or AI services.
- 7-8: Strong match — strategic consulting, policy advisory, digital
  transformation, public affairs, advocacy-adjacent.
- 4-6: Tangential — communications, marketing, general consulting,
  research services. Could be a fit for EMC's broader capability.
- 1-3: Unrelated — construction, supplies, equipment, food service,
  janitorial, fleet maintenance, athletic equipment, vehicles, etc.

Do NOT default to 5. Most government RFPs are unrelated (1-3). Reserve high
scores (7+) for genuine service-line matches.

Return [] if no RFPs in the text. Always valid JSON.
"""


def fetch_via_jina(url: str, char_limit: int = 6000, timeout: int = 20) -> str:
    """Fetch a URL through Jina Reader for clean text extraction."""
    jina_url = f"{JINA_BASE}/{quote(url, safe='')}"
    r = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=timeout)
    r.raise_for_status()
    return r.text[:char_limit]


def analyze_with_perplexity(text: str, context: str = "") -> list[dict]:
    """Send raw text to Perplexity, get a list of structured RFPs back."""
    if not PERPLEXITY_API_KEY:
        raise RuntimeError("PERPLEXITY_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\n\n{text}"},
        ],
        "temperature": 0.1,
    }

    r = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers=headers,
        json=payload,
        timeout=45,
    )
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]

    cleaned = re.sub(r"```json\s*|```\s*", "", raw).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            return []

    return parsed if isinstance(parsed, list) else [parsed]
