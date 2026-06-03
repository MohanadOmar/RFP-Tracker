"""Perplexity client — used by sources that need AI extraction."""
import os
import json
import re
import requests

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
JINA_BASE = os.environ.get("JINA_BASE_URL", "https://r.jina.ai/").rstrip("/")

SYSTEM_PROMPT = """You are an RFP analyst for EMC Strategy Group, a Texas lobbying and government relations firm.

Extract all RFPs found in the provided text and return a JSON ARRAY only — no markdown, no preamble, no commentary.

Each item must have these exact keys:
{
  "solicitation_id": "string (the unique ID, often a code like RFP-2026-001)",
  "title": "string",
  "description": "string (1-3 sentence summary)",
  "agency": "string (issuing organization)",
  "contact_info": "string (email or phone if found, else empty)",
  "deadline": "YYYY-MM-DD or null",
  "prebid_date": "YYYY-MM-DD or null",
  "url": "string (direct link if found, else empty)",
  "requirements": ["string", "string"],
  "relevance_score": <integer 1-10>,
  "relevance_reason": "string explaining the score"
}

Score relevance for a lobbying / government relations / legislative advocacy firm.
High score (8-10): direct lobbying, government affairs, legislative consulting.
Medium (5-7): public affairs, intergovernmental relations, policy consulting.
Low (1-4): unrelated services like construction, IT, supplies.

Return [] if no RFPs are found. Always valid JSON.
"""


def fetch_via_jina(url: str, char_limit: int = 6000, timeout: int = 20) -> str:
    """Fetch a URL through Jina Reader for clean text extraction."""
    from urllib.parse import quote
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
        parsed = json.loads(match.group())

    return parsed if isinstance(parsed, list) else [parsed]
