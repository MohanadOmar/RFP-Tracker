"""Perplexity client — Jina fetch + RFP analysis helpers.

Two analysis functions:
- analyze_with_perplexity: legacy, extracts MANY RFPs from a listing page
- analyze_single_rfp_detail: NEW, scores a single RFP from its detail page
"""
import os
import json
import re
import time
import requests
from urllib.parse import quote

PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")
JINA_BASE = os.environ.get("JINA_BASE_URL", "https://r.jina.ai/").rstrip("/")
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

# Throttle between Jina calls (seconds). Lower if you have an API key.
JINA_THROTTLE_SECONDS = float(os.environ.get("JINA_THROTTLE_SECONDS", "0.3" if JINA_API_KEY else "2.5"))
_last_jina_call = 0.0

# ---- Shared prompt fragments ----

EMC_SERVICES_BLOCK = """
EMC Strategy Group's services:
- Lobbying and legislative advocacy
- Grant writing and grant consulting
- Government relations and public affairs
- Web development and website design
- AI integrations and machine learning consulting
"""

SCORING_RUBRIC = """
SCORING GUIDE (be discriminating, not generous):
- 9-10: Direct match — explicitly asks for lobbying, government relations,
  legislative advocacy, grants consulting, web development, or AI services.
- 7-8: Strong match — strategic consulting, policy advisory, digital
  transformation, public affairs, advocacy-adjacent.
- 4-6: Tangential — communications, marketing, general consulting,
  research services. Could fit EMC's broader capability.
- 1-3: Unrelated — construction, supplies, equipment, food service,
  janitorial, fleet maintenance, athletic equipment, vehicles, etc.

Do NOT default to 5. Most government RFPs are unrelated (1-3). Reserve
high scores (7+) for genuine service-line matches.
"""

# ---- Listing extraction (still used by samgov/bidnet/civcast) ----

LIST_SYSTEM_PROMPT = f"""You are an RFP analyst for EMC Strategy Group.

{EMC_SERVICES_BLOCK}

Extract every RFP from the provided text. Return a JSON ARRAY only — no
markdown, no preamble. Each item:

{{
  "solicitation_id": "string",
  "title": "string",
  "description": "string (1-3 sentences)",
  "agency": "string",
  "contact_info": "string",
  "deadline": "YYYY-MM-DD or null",
  "prebid_date": "YYYY-MM-DD or null",
  "url": "string",
  "requirements": ["string"],
  "category": "Lobbying | Grants | Government Relations | Web Development | AI | Other",
  "relevance_score": <integer 1-10>,
  "relevance_reason": "string"
}}

{SCORING_RUBRIC}

CATEGORY: pick the ONE best fit from Lobbying, Grants, Government Relations,
Web Development, AI, or Other. Default to "Other" only when nothing fits.

Return [] if no RFPs. Always valid JSON.
"""

# ---- Detail-page scoring (new, used by txsmartbuy) ----

DETAIL_SYSTEM_PROMPT = f"""You are an RFP analyst for EMC Strategy Group.

{EMC_SERVICES_BLOCK}

You will be given the DETAIL PAGE of a single RFP. Read carefully and return
ONE JSON OBJECT (not an array). Extract everything you can find. If a field
isn't in the text, use empty string or null.

{{
  "title": "string (the RFP title or subject)",
  "description": "string (2-4 sentence summary of what the RFP is for)",
  "agency": "string (issuing agency or organization)",
  "contact_info": "string (name + email/phone if present)",
  "deadline": "YYYY-MM-DD or null",
  "prebid_date": "YYYY-MM-DD or null",
  "requirements": ["string", "string"],
  "category": "string (see CATEGORY GUIDE below)",
  "relevance_score": <integer 1-10>,
  "relevance_reason": "string explaining the score"
}}

{SCORING_RUBRIC}

CATEGORY GUIDE — pick the ONE best fit:
- "Lobbying" — lobbying services, legislative advocacy, government affairs,
  legislative representation, political consulting
- "Grants" — grant writing, grant consulting, grant management, grant programs
- "Government Relations" — public affairs, intergovernmental relations,
  policy consulting, strategic government engagement
- "Web Development" — websites, web apps, portals, digital platforms,
  web design, web modernization
- "AI" — AI integration, machine learning, automation, chatbots,
  data platforms with AI/ML components
- "Other" — anything else (still return a relevance_score honestly)

If the RFP could fit two categories, pick the one most central to the work
described. Default to "Other" only when no service category fits.

If the detail page is mostly empty (e.g. just says "see attached PDF"), score
based on the title and any visible scope text. Note in relevance_reason that
the detail page was sparse.

Return valid JSON only. No markdown.
"""


# ---- Fetching ----

def fetch_via_jina(url: str, char_limit: int = 6000, timeout: int = 30) -> str:
    """Fetch a URL through Jina Reader for clean text extraction.

    Uses JINA_API_KEY if set (higher rate limits + larger free quota).
    Throttles between calls to respect Jina's rate limits.
    Retries once on 429 with extra backoff.
    """
    global _last_jina_call

    # Throttle: ensure minimum gap between calls
    now = time.time()
    elapsed = now - _last_jina_call
    if elapsed < JINA_THROTTLE_SECONDS:
        time.sleep(JINA_THROTTLE_SECONDS - elapsed)

    jina_url = f"{JINA_BASE}/{quote(url, safe='')}"
    headers = {"Accept": "text/plain"}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"

    for attempt in range(2):
        _last_jina_call = time.time()
        r = requests.get(jina_url, headers=headers, timeout=timeout)
        if r.status_code == 429 and attempt == 0:
            # Got rate-limited despite throttling — back off harder and retry once
            print(f"  Jina 429, backing off 10s before retry...")
            time.sleep(10)
            continue
        r.raise_for_status()
        return r.text[:char_limit]

    r.raise_for_status()  # final fail
    return ""


def _call_perplexity(system_prompt: str, user_content: str, timeout: int = 45) -> str:
    if not PERPLEXITY_API_KEY:
        raise RuntimeError("PERPLEXITY_API_KEY not set")

    r = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sonar",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _strip_json_fences(raw: str) -> str:
    return re.sub(r"```json\s*|```\s*", "", raw).strip()


# ---- Public analysis functions ----

def analyze_with_perplexity(text: str, context: str = "") -> list[dict]:
    """Extract MANY RFPs from a listing page (used by samgov/bidnet/civcast)."""
    raw = _call_perplexity(LIST_SYSTEM_PROMPT, f"{context}\n\n{text}")
    cleaned = _strip_json_fences(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if not m:
            return []
        try:
            parsed = json.loads(m.group())
        except json.JSONDecodeError:
            return []

    return parsed if isinstance(parsed, list) else [parsed]


def analyze_single_rfp_detail(
    detail_text: str,
    sid: str,
    agency_name: str,
    agency_number: int,
) -> dict:
    """Score a single RFP from its detail page. Returns one dict."""
    user_content = (
        f"Solicitation ID: {sid}\n"
        f"Issuing Agency: {agency_name} (Texas SmartBuy member {agency_number})\n"
        f"Source: Texas SmartBuy ESBD detail page\n\n"
        f"--- DETAIL PAGE CONTENT ---\n{detail_text}"
    )
    raw = _call_perplexity(DETAIL_SYSTEM_PROMPT, user_content)
    cleaned = _strip_json_fences(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                parsed = {}
        else:
            parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    # Always provide minimum fields so caller doesn't crash
    parsed.setdefault("title", f"Solicitation {sid}")
    parsed.setdefault("agency", agency_name)
    parsed.setdefault("relevance_score", 0)
    parsed.setdefault("relevance_reason", "Detail page parse failed")
    parsed.setdefault("category", "Other")

    # Validate category — fall back to Other if Perplexity returns garbage
    VALID_CATEGORIES = {"Lobbying", "Grants", "Government Relations",
                        "Web Development", "AI", "Other"}
    if parsed.get("category") not in VALID_CATEGORIES:
        parsed["category"] = "Other"

    return parsed
