"""TX SmartBuy / ESBD — V2 pipeline.

For each agency:
  1. Fetch listing page via Jina, regex-extract IDs (no Perplexity)
  2. Filter out already-seen IDs + out-of-window IDs (FREE)
  3. For each NEW RFP:
       a. Fetch detail page HTML via requests + BS4 (structured metadata)
       b. Find first PDF, extract text via Jina
       c. Fall back to HTML description if PDF unavailable
       d. Score the PDF/description text via Perplexity

Safety cap: max 100 Perplexity calls per run.
"""
import re
from datetime import datetime, timedelta
from perplexity_client import fetch_via_jina, analyze_single_rfp_detail
from detail_extractor import extract_content_for_scoring

SOURCE_NAME = "TX SmartBuy"
BASE_URL = "https://www.txsmartbuy.gov/esbd"
MAX_PAGES_PER_AGENCY = 3
MAX_DAYS_OUT = 90
GLOBAL_PERPLEXITY_CAP = 100

AGENCIES = [
    # (302, "Office of the Governor"),
    # (304, "Comptroller of Public Accounts"),
    # (313, "Department of Information Resources"),
    # (405, "Department of Public Safety"),
    # (551, "Department of Agriculture"),
    # (582, "Commission on Environmental Quality"),
    # (696, "Department of Criminal Justice"),
    (701, "Texas Education Agency"),
    (720, "University of Texas System"),
    (781, "Higher Education Coordinating Board"),
]

NO_RESULTS_MARKER = "No results found"
_perplexity_calls_this_run = 0


def _build_listing_url(agency_number: int, page: int) -> str:
    return (
        f"{BASE_URL}?status=1"
        f"&memberNumber={agency_number}"
        f"&page={page}"
        f"&agencyNumber={agency_number}"
    )


def _parse_listing(text: str) -> list[dict]:
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


def _parse_us_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y")
    except ValueError:
        return None


def _is_within_window(due_date_raw: str | None) -> bool:
    d = _parse_us_date(due_date_raw)
    if not d:
        return True
    today = datetime.today()
    cutoff = today + timedelta(days=MAX_DAYS_OUT)
    return today <= d <= cutoff


def fetch(existing_ids: set[str] | None = None) -> list[dict]:
    """V2 pipeline: listing → dedup → PDF/HTML extraction → Perplexity scoring."""
    global _perplexity_calls_this_run
    _perplexity_calls_this_run = 0
    existing_ids = existing_ids or set()
    all_new_rfps = []

    for agency_number, agency_name in AGENCIES:
        if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
            print(f"[{SOURCE_NAME}] Reached global cap. Stopping.")
            break

        print(f"[{SOURCE_NAME}] Agency {agency_number} — {agency_name}")

        for page in range(1, MAX_PAGES_PER_AGENCY + 1):
            listing_url = _build_listing_url(agency_number, page)

            try:
                text = fetch_via_jina(listing_url, char_limit=12000, timeout=25)
            except Exception as e:
                print(f"  Page {page} listing fetch error: {e}")
                break

            if NO_RESULTS_MARKER in text:
                print(f"  Page {page}: no results, next agency")
                break

            listings = _parse_listing(text)
            print(f"  Page {page}: parsed {len(listings)} listings")

            if not listings:
                break

            # Stage 2: dedup + date filter (free)
            candidates = []
            skipped_dup = 0
            skipped_date = 0
            for L in listings:
                sid = L["solicitation_id"]
                if sid in existing_ids:
                    skipped_dup += 1
                    continue
                if not _is_within_window(L["due_date_raw"]):
                    skipped_date += 1
                    continue
                candidates.append(L)

            print(f"    {len(candidates)} new (skipped {skipped_dup} dup, {skipped_date} out-of-window)")

            # Stage 3: for each NEW candidate — detail + PDF + score
            for L in candidates:
                if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
                    print(f"  Reached global cap mid-page. Stopping.")
                    break

                sid = L["solicitation_id"]

                # Get full detail (HTML metadata + PDF text or HTML fallback)
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

                # Score with Perplexity
                try:
                    scored = analyze_single_rfp_detail(
                        scoring_text,
                        sid=sid,
                        agency_name=agency_name,
                        agency_number=agency_number,
                    )
                    _perplexity_calls_this_run += 1
                except Exception as e:
                    print(f"    {sid} Perplexity error: {e}")
                    continue

                # Merge HTML metadata into scored result.
                # HTML wins for structured fields; Perplexity wins for analysis.
                scored["solicitation_id"] = sid
                scored["source"] = SOURCE_NAME
                scored["url"] = detail.get("detail_url", f"{BASE_URL}/{sid}")
                scored["agency_number"] = agency_number

                # Prefer HTML-parsed values when present
                if detail.get("title"):
                    scored["title"] = detail["title"]
                if detail.get("contact_info"):
                    scored["contact_info"] = detail["contact_info"]
                if detail.get("deadline"):
                    scored["deadline"] = detail["deadline"]
                if not scored.get("agency"):
                    scored["agency"] = agency_name

                # Add scoring provenance
                scored["scoring_source"] = source_type  # "pdf" | "html" | "none"
                scored["pdf_url"] = content.get("pdf_used")

                existing_ids.add(sid)
                all_new_rfps.append(scored)

                indicator = "📄" if source_type == "pdf" else "📝"
                print(f"    {indicator} Scored {sid}: {scored.get('relevance_score', '?')}/10 — {scored.get('title', '')[:50]}")

    print(f"[{SOURCE_NAME}] Done. {len(all_new_rfps)} new RFPs, {_perplexity_calls_this_run} Perplexity calls")
    return all_new_rfps
