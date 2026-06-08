"""TX SmartBuy / ESBD — V3 pipeline: NIGP code-driven search.

Instead of looping through agencies, we loop through EMC-relevant NIGP codes.
This pulls RFPs from EVERY Texas entity using NIGP (state agencies, cities,
school districts, libraries, river authorities, universities, etc.) that
posted work in EMC's service categories.

Pipeline per NIGP code:
  1. Fetch listing via Jina: txsmartbuy.gov/esbd?nigp={code}&page={n}
  2. Regex-extract IDs + due dates + status (no Perplexity)
  3. Filter: dedup, due-date window, drop Closed statuses
  4. For each NEW RFP:
       a. BS4 detail page extraction (HTML + PDF URL)
       b. Jina PDF text extraction (first 8000 chars)
       c. Pass content + NIGP-suggested category to Perplexity
       d. Perplexity scores + may override the suggested category

Safety cap: max 100 Perplexity calls per run.
"""
import re
from datetime import datetime, timedelta
from perplexity_client import fetch_via_jina, analyze_single_rfp_detail
from detail_extractor import extract_content_for_scoring

SOURCE_NAME = "TX SmartBuy"
BASE_URL = "https://www.txsmartbuy.gov/esbd"
MAX_PAGES_PER_CODE = 5
MAX_DAYS_OUT = 90
GLOBAL_PERPLEXITY_CAP = 100
NO_RESULTS_MARKER = "No results found"

# EMC-relevant NIGP codes with their default category assignment.
# Categories: Lobbying, Grants, Government Relations, Web Development, AI, Other
NIGP_CODES = [
    # (code, description, default_category)

    # --- Government Relations / Lobbying ---
    ("91858", "Governmental Consulting", "Government Relations"),
    ("91826", "Communications: Public Relations Consulting", "Government Relations"),
    ("91827", "Community Development Consulting", "Government Relations"),
    ("96153", "Marketing Service, Public Opinion Surveys, Research", "Government Relations"),
    ("91871", "Management Consulting", "Government Relations"),

    # --- Grants ---
    ("91846", "Feasibility Studies (Consulting)", "Grants"),
    ("94649", "Financial Services", "Grants"),
    ("95877", "Project Management Services", "Grants"),

    # --- Web Development ---
    ("91596", "Web Page Design, Management and Maintenance Services", "Web Development"),
    ("20872", "Software, Internet/Web-based", "Web Development"),

    # --- AI / IT Consulting ---
    ("91595", "Information Technology Consulting", "AI"),
    ("91829", "Computer Software Consulting", "AI"),
    ("91830", "Computer Network Consulting", "AI"),
    ("92038", "Database Software", "AI"),
]

_perplexity_calls_this_run = 0


def _build_listing_url(nigp_code: str, page: int) -> str:
    return f"{BASE_URL}?nigp={nigp_code}&page={page}"


def _parse_listing(text: str) -> list:
    """Extract solicitation records from listing page via regex."""
    records = []
    blocks = re.split(r"\*\*Solicitation ID:\*\*", text)

    for block in blocks[1:]:
        sol_id_match = re.match(r"\s*([\w\-]+)", block)
        if not sol_id_match:
            continue
        sol_id = sol_id_match.group(1).strip()

        due_match = re.search(r"\*\*Due Date:\*\*\s*(\d{1,2}/\d{1,2}/\d{4})", block)
        status_match = re.search(r"\*\*Status:\*\*\s*([^\n*]+)", block)
        posted_match = re.search(r"\*\*Posting Date:\*\*\s*(\d{1,2}/\d{1,2}/\d{4})", block)

        records.append({
            "solicitation_id": sol_id,
            "due_date_raw": due_match.group(1) if due_match else None,
            "status": status_match.group(1).strip() if status_match else "",
            "posting_date_raw": posted_match.group(1) if posted_match else None,
        })

    return records


def _parse_us_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y")
    except ValueError:
        return None


def _is_within_window(due_date_raw):
    d = _parse_us_date(due_date_raw)
    if not d:
        return True
    today = datetime.today()
    cutoff = today + timedelta(days=MAX_DAYS_OUT)
    return today <= d <= cutoff


def _is_active_status(status):
    """Drop closed / cancelled / withdrawn solicitations."""
    if not status:
        return True
    s = status.lower()
    dead_markers = ("closed", "cancel", "withdraw", "awarded", "complete")
    return not any(m in s for m in dead_markers)


def _pick_primary_category(matches):
    """When multiple NIGP codes match, pick a sensible default category."""
    if not matches:
        return "Other"
    priority = ["Lobbying", "Government Relations", "Grants",
                "Web Development", "AI", "Other"]
    cats_found = {m["category"] for m in matches}
    for cat in priority:
        if cat in cats_found:
            return cat
    return "Other"


def _format_nigp_context(matches):
    """Format matched NIGP codes as a context block for Perplexity."""
    if not matches:
        return ""
    lines = ["This RFP is tagged with the following NIGP commodity codes:"]
    for m in matches:
        lines.append(f"  - {m['code']}: {m['description']} (suggests {m['category']})")
    lines.append(
        "Trust these classifications when assigning category, but you may "
        "override if the PDF content makes a different category clearly correct."
    )
    return "\n".join(lines)


def fetch(existing_ids=None):
    """V3 pipeline: iterate NIGP codes, not agencies."""
    global _perplexity_calls_this_run
    _perplexity_calls_this_run = 0
    existing_ids = existing_ids or set()
    seen_this_run = set()
    sol_id_to_matches = {}
    all_new_rfps = []

    for nigp_code, description, default_category in NIGP_CODES:
        if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
            print(f"[{SOURCE_NAME}] Reached global cap. Stopping.")
            break

        print(f"[{SOURCE_NAME}] NIGP {nigp_code} — {description}")

        for page in range(1, MAX_PAGES_PER_CODE + 1):
            listing_url = _build_listing_url(nigp_code, page)

            try:
                text = fetch_via_jina(listing_url, char_limit=15000, timeout=25)
            except Exception as e:
                print(f"  Page {page} listing fetch error: {e}")
                break

            if NO_RESULTS_MARKER in text:
                print(f"  Page {page}: no results, next NIGP code")
                break

            listings = _parse_listing(text)
            print(f"  Page {page}: parsed {len(listings)} listings")

            if not listings:
                break

            candidates = []
            skipped_dup = 0
            skipped_date = 0
            skipped_status = 0
            for L in listings:
                sid = L["solicitation_id"]

                sol_id_to_matches.setdefault(sid, []).append({
                    "code": nigp_code,
                    "description": description,
                    "category": default_category,
                })

                if sid in existing_ids:
                    skipped_dup += 1
                    continue
                if sid in seen_this_run:
                    skipped_dup += 1
                    continue
                if not _is_within_window(L["due_date_raw"]):
                    skipped_date += 1
                    continue
                if not _is_active_status(L["status"]):
                    skipped_status += 1
                    continue
                candidates.append(L)

            print(f"    {len(candidates)} new (skipped {skipped_dup} dup, "
                  f"{skipped_date} out-of-window, {skipped_status} closed)")

            for L in candidates:
                if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
                    print(f"  Reached global cap mid-page. Stopping.")
                    break

                sid = L["solicitation_id"]

                try:
                    content = extract_content_for_scoring(sid)
                except Exception as e:
                    print(f"    {sid} extraction error: {e}")
                    continue

                detail = content["detail"]
                scoring_text = content["scoring_text"]
                source_type = content["scoring_source"]

                if not scoring_text or len(scoring_text.strip()) < 50:
                    print(f"    {sid} skipped — no content to score")
                    continue

                matched_codes = sol_id_to_matches.get(sid, [])
                nigp_context = _format_nigp_context(matched_codes)
                suggested_cat = _pick_primary_category(matched_codes)

                try:
                    scored = analyze_single_rfp_detail(
                        scoring_text,
                        sid=sid,
                        agency_name=detail.get("agency_member_number", "")
                            or "Texas SmartBuy member",
                        agency_number=0,
                        nigp_context=nigp_context,
                        suggested_category=suggested_cat,
                    )
                    _perplexity_calls_this_run += 1
                except Exception as e:
                    print(f"    {sid} Perplexity error: {e}")
                    continue

                scored["solicitation_id"] = sid
                scored["source"] = SOURCE_NAME
                scored["url"] = detail.get("detail_url", f"{BASE_URL}/{sid}")
                if detail.get("title"):
                    scored["title"] = detail["title"]
                if detail.get("contact_info"):
                    scored["contact_info"] = detail["contact_info"]
                if detail.get("deadline"):
                    scored["deadline"] = detail["deadline"]
                if not scored.get("agency"):
                    scored["agency"] = f"Texas SmartBuy member {detail.get('agency_member_number', '?')}"

                scored["scoring_source"] = source_type
                scored["pdf_url"] = content.get("pdf_used")
                scored["nigp_matches"] = ", ".join(
                    f"{m['code']} ({m['description']})" for m in matched_codes
                )

                seen_this_run.add(sid)
                all_new_rfps.append(scored)

                indicator = "📄" if source_type == "pdf" else "📝"
                codes_str = "/".join(m['code'] for m in matched_codes)
                print(f"    {indicator} Scored {sid} [{codes_str}]: "
                      f"{scored.get('relevance_score', '?')}/10 "
                      f"({scored.get('category', '?')}) — "
                      f"{scored.get('title', '')[:50]}")

    print(f"[{SOURCE_NAME}] Done. {len(all_new_rfps)} new RFPs, "
          f"{_perplexity_calls_this_run} Perplexity calls")
    return all_new_rfps
