"""TX SmartBuy / ESBD — agency-by-agency fetch with smart dedup.

Pipeline:
1. Fetch listing page via Jina (cheap)
2. Regex-extract IDs + metadata from listing (FREE — no Perplexity)
3. Filter out RFPs already in Base44 DB (FREE)
4. Filter out RFPs with due date > 90 days out (FREE)
5. For each NEW RFP: fetch detail page + Perplexity score (expensive, but only on new ones)

Safety cap: max 100 Perplexity calls per run.
"""
import re
from datetime import datetime, timedelta
from perplexity_client import fetch_via_jina, analyze_single_rfp_detail

SOURCE_NAME = "TX SmartBuy"
BASE_URL = "https://www.txsmartbuy.gov/esbd"
MAX_PAGES_PER_AGENCY = 3
MAX_DAYS_OUT = 90
GLOBAL_PERPLEXITY_CAP = 100  # safety net per run

AGENCIES = [
    (302, "Office of the Governor"),
    (303, "Texas Facilities Commission"),
    (304, "Comptroller of Public Accounts"),
    (313, "Department of Information Resources"),
    (405, "Department of Public Safety"),
    (529, "Health and Human Services Commission"),
    (537, "Department of State Health Services"),
    (551, "Department of Agriculture"),
    (582, "Commission on Environmental Quality"),
    (601, "Department of Transportation"),
    (696, "Department of Criminal Justice"),
    (701, "Texas Education Agency"),
    (720, "University of Texas System"),
    (781, "Higher Education Coordinating Board"),
]

NO_RESULTS_MARKER = "No results found"

# Module-level counter resets each fetch() call
_perplexity_calls_this_run = 0


def _build_listing_url(agency_number: int, page: int) -> str:
    return (
        f"{BASE_URL}?status=1"
        f"&memberNumber={agency_number}"
        f"&page={page}"
        f"&agencyNumber={agency_number}"
    )


def _build_detail_url(solicitation_id: str) -> str:
    return f"{BASE_URL}/{solicitation_id}"


def _parse_listing(text: str) -> list[dict]:
    """Extract solicitation records from listing page text via regex.

    Each record in the listing looks like:
      **Solicitation ID:** 405-26R0018465
      **Due Date:** 6/10/2026
      **Due Time:** 5:00 PM
      **Agency/Texas SmartBuy Member Number:** 405
      **Status:** Posted
      **Posting Date:** 5/14/2026
    """
    records = []
    # Split into blocks by Solicitation ID marker
    blocks = re.split(r"\*\*Solicitation ID:\*\*", text)

    for block in blocks[1:]:  # skip header before first ID
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
    """Parse M/D/YYYY to datetime, or None if invalid."""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%m/%d/%Y")
    except ValueError:
        return None


def _to_iso_date(s: str) -> str | None:
    """Convert M/D/YYYY → YYYY-MM-DD for Base44 date field."""
    d = _parse_us_date(s)
    return d.strftime("%Y-%m-%d") if d else None


def _is_within_window(due_date_raw: str | None) -> bool:
    """True if due date is in the future and within MAX_DAYS_OUT days."""
    d = _parse_us_date(due_date_raw)
    if not d:
        return True  # if we can't parse, keep it rather than drop it
    today = datetime.today()
    cutoff = today + timedelta(days=MAX_DAYS_OUT)
    return today <= d <= cutoff


def fetch(existing_ids: set[str] | None = None) -> list[dict]:
    """Fetch new RFPs across all priority agencies.

    existing_ids: set of solicitation_ids already in Base44, used to skip
    re-fetching and re-scoring known RFPs.
    """
    global _perplexity_calls_this_run
    _perplexity_calls_this_run = 0
    existing_ids = existing_ids or set()
    all_new_rfps = []

    for agency_number, agency_name in AGENCIES:
        if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
            print(f"[{SOURCE_NAME}] Reached global cap of {GLOBAL_PERPLEXITY_CAP} Perplexity calls. Stopping.")
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

            # Stage 1: regex parse (free)
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

            # Stage 3: deep-fetch + score each NEW candidate
            for L in candidates:
                if _perplexity_calls_this_run >= GLOBAL_PERPLEXITY_CAP:
                    print(f"  Reached global cap mid-page. Stopping.")
                    break

                sid = L["solicitation_id"]
                detail_url = _build_detail_url(sid)

                try:
                    detail_text = fetch_via_jina(detail_url, char_limit=6000, timeout=20)
                except Exception as e:
                    print(f"    {sid} detail fetch error: {e}")
                    continue

                try:
                    scored = analyze_single_rfp_detail(
                        detail_text,
                        sid=sid,
                        agency_name=agency_name,
                        agency_number=agency_number,
                    )
                    _perplexity_calls_this_run += 1
                except Exception as e:
                    print(f"    {sid} Perplexity error: {e}")
                    continue

                # Enrich with metadata from listing
                scored["solicitation_id"] = sid
                scored["source"] = SOURCE_NAME
                scored["url"] = detail_url
                scored["agency_number"] = agency_number
                if not scored.get("agency"):
                    scored["agency"] = agency_name
                if not scored.get("deadline") and L.get("due_date_raw"):
                    scored["deadline"] = _to_iso_date(L["due_date_raw"])

                # Mark in-memory so dedup works within this run too
                existing_ids.add(sid)

                all_new_rfps.append(scored)
                print(f"    Scored {sid}: {scored.get('relevance_score', '?')}/10 — {scored.get('title', '')[:50]}")

    print(f"[{SOURCE_NAME}] Done. {len(all_new_rfps)} new RFPs, {_perplexity_calls_this_run} Perplexity calls")
    return all_new_rfps
