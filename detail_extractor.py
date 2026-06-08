"""TX SmartBuy detail-page + PDF extractor.

Two responsibilities:
1. Fetch the detail page HTML (requests, server-rendered)
2. Parse structured fields with BeautifulSoup
3. Find the first PDF attachment, fetch its text via Jina

If PDF extraction fails, returns the HTML description as fallback so
Perplexity still gets something useful to score.
"""
import re
import requests
from bs4 import BeautifulSoup
from perplexity_client import fetch_via_jina

DETAIL_BASE = "https://www.txsmartbuy.gov/esbd"
FILE_BASE   = "https://www.txsmartbuy.gov"
DEFAULT_TIMEOUT = 20
PDF_CHAR_LIMIT  = 8000


def _build_pdf_url(data_href: str) -> str:
    """Detail pages encode the file href; normalize and prefix with the host."""
    if not data_href:
        return ""
    href = data_href.replace("&amp;", "&").strip()
    if href.startswith("http"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return f"{FILE_BASE}{href}"


def _text_after(label_node) -> str:
    """Given a <strong>Label:</strong> node, return the trimmed text of the
    following <p>. Returns empty string if not found."""
    if not label_node:
        return ""
    p = label_node.find_next("p")
    return p.get_text(strip=True) if p else ""


def _parse_us_date(s: str) -> str | None:
    """Convert M/D/YYYY to YYYY-MM-DD."""
    if not s:
        return None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    mo, da, yr = m.groups()
    return f"{yr}-{int(mo):02d}-{int(da):02d}"


def fetch_detail_page(sid: str) -> dict:
    """Return structured fields parsed from the detail page HTML.

    Output keys:
      - title, status, contact_name, contact_phone, contact_email,
        bid_response_email, deadline, agency_member_number,
        posting_date, description, nigp_codes, pdf_urls (list),
        detail_url
    Missing fields are empty strings or empty lists.
    """
    detail_url = f"{DETAIL_BASE}/{sid}"
    try:
        r = requests.get(detail_url, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        return {"detail_url": detail_url, "error": f"HTML fetch failed: {e}"}

    soup = BeautifulSoup(r.text, "html.parser")
    out: dict = {"detail_url": detail_url}

    # Title: <h4>RFO 701-26-006 PHYSICAL FITNESS ASSESSMENT</h4>
    h4 = soup.select_one(".esbd-result-title h4")
    out["title"] = h4.get_text(strip=True) if h4 else ""

    # Iterate every <div class="esbd-result-cell"> and map label → value
    field_map = {
        "Solicitation ID": "solicitation_id",
        "Status": "status",
        "Contact Name": "contact_name",
        "Contact Number": "contact_phone",
        "Contact Email": "contact_email",
        "Bid Response Email": "bid_response_email",
        "Response Due Date": "deadline_raw",
        "Response Due Time": "deadline_time",
        "Agency/Texas SmartBuy Member Number": "agency_member_number",
        "Solicitation Posting Date": "posting_date_raw",
        "Last Modified": "last_modified",
        "Class/Item Code": "nigp_codes",
    }
    for cell in soup.select(".esbd-result-cell"):
        strong = cell.find("strong")
        if not strong:
            continue
        label = strong.get_text(strip=True).rstrip(":").strip()
        key = field_map.get(label)
        if not key:
            continue
        p = cell.find("p")
        out[key] = p.get_text(strip=True) if p else ""

    # Description: <strong>Solicitation Description:</strong> followed by
    # <div class="rich-text-editor-content">
    desc_div = soup.select_one(".rich-text-editor-content")
    if desc_div:
        out["description"] = desc_div.get_text(" ", strip=True)
    else:
        out["description"] = ""

    # Build a combined contact_info string for Base44
    contact_parts = []
    if out.get("contact_name"):
        contact_parts.append(out["contact_name"])
    if out.get("contact_email"):
        contact_parts.append(out["contact_email"])
    if out.get("contact_phone"):
        contact_parts.append(out["contact_phone"])
    out["contact_info"] = " — ".join(contact_parts)

    # Normalize deadline date
    out["deadline"] = _parse_us_date(out.get("deadline_raw", ""))

    # PDF attachments — collect all, preserving order
    pdf_links = []
    for a in soup.select("a[data-action='downloadURL']"):
        href = a.get("data-href", "")
        name = a.get_text(strip=True)
        url = _build_pdf_url(href)
        if url:
            pdf_links.append({"name": name, "url": url})
    out["pdf_links"] = pdf_links

    return out


def fetch_pdf_text(pdf_url: str) -> str:
    """Use Jina to extract text from the PDF URL. First 8000 chars."""
    return fetch_via_jina(pdf_url, char_limit=PDF_CHAR_LIMIT, timeout=30)


def extract_content_for_scoring(sid: str) -> dict:
    """Top-level entry: returns everything needed to score one RFP.

    Output:
      {
        "detail": <dict from fetch_detail_page>,
        "scoring_text": <str — the text fed to Perplexity>,
        "scoring_source": "pdf" | "html" | "none",
        "pdf_used": <url or None>,
      }
    """
    detail = fetch_detail_page(sid)
    pdf_links = detail.get("pdf_links", [])

    # Strategy: take the FIRST PDF (usually the main solicitation doc).
    if pdf_links:
        first_pdf = pdf_links[0]
        try:
            pdf_text = fetch_pdf_text(first_pdf["url"])
            if pdf_text and len(pdf_text.strip()) > 200:
                return {
                    "detail": detail,
                    "scoring_text": pdf_text,
                    "scoring_source": "pdf",
                    "pdf_used": first_pdf["url"],
                    "pdf_name": first_pdf["name"],
                }
        except Exception as e:
            print(f"    PDF fetch failed for {sid}: {e}")

    # Fallback: HTML description
    desc = detail.get("description", "")
    if desc:
        return {
            "detail": detail,
            "scoring_text": desc,
            "scoring_source": "html",
            "pdf_used": None,
            "pdf_name": None,
        }

    # Nothing usable
    return {
        "detail": detail,
        "scoring_text": detail.get("title", ""),
        "scoring_source": "none",
        "pdf_used": None,
        "pdf_name": None,
    }
