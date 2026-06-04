"""TX SmartBuy / ESBD — fetch RFPs by agency number, paginated.

Loops through priority agencies for EMC Strategy Group. For each agency,
fetches up to 3 pages. Stops when the page shows "No results found".
Each RFP goes to Perplexity for deep analysis + relevance scoring.
"""
from perplexity_client import fetch_via_jina, analyze_with_perplexity

SOURCE_NAME = "TX SmartBuy"
BASE_URL = "https://www.txsmartbuy.gov/esbd"
MAX_PAGES_PER_AGENCY = 3

# Priority agencies for EMC: lobbying, grants, government relations, web dev, AI
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


def _build_url(agency_number: int, page: int) -> str:
    return (
        f"{BASE_URL}?status=1"
        f"&memberNumber={agency_number}"
        f"&page={page}"
        f"&agencyNumber={agency_number}"
    )


def fetch() -> list[dict]:
    """Fetch all current RFPs across all priority agencies."""
    all_rfps = []

    for agency_number, agency_name in AGENCIES:
        print(f"[{SOURCE_NAME}] Agency {agency_number} — {agency_name}")

        for page in range(1, MAX_PAGES_PER_AGENCY + 1):
            url = _build_url(agency_number, page)

            try:
                text = fetch_via_jina(url, char_limit=7000, timeout=20)
            except Exception as e:
                print(f"  Page {page} fetch error: {e}")
                break

            if NO_RESULTS_MARKER in text:
                print(f"  Page {page}: no results, moving to next agency")
                break

            try:
                rfps = analyze_with_perplexity(
                    text,
                    context=(
                        f"Source: Texas SmartBuy ESBD. "
                        f"Agency {agency_number} ({agency_name}), page {page}. "
                        f"Extract every open RFP visible. Score each 1-10 for "
                        f"relevance to EMC Strategy Group, which offers: lobbying, "
                        f"grants consulting, government relations, web development, "
                        f"AI integrations."
                    ),
                )
            except Exception as e:
                print(f"  Page {page} Perplexity error: {e}")
                continue

            for r in rfps:
                r["source"] = SOURCE_NAME
                r["agency_number"] = agency_number
                if not r.get("agency"):
                    r["agency"] = agency_name
                if not r.get("url") and r.get("solicitation_id"):
                    r["url"] = f"{BASE_URL}/{r['solicitation_id']}"

            all_rfps.extend(rfps)
            print(f"  Page {page}: {len(rfps)} RFPs extracted")

    print(f"[{SOURCE_NAME}] Total: {len(all_rfps)} RFPs across all agencies")
    return all_rfps
