"""Wrapper for Base44 REST API. Handles RFP and JobLog entities."""
import os
import requests

BASE44_API_URL = os.environ.get("BASE44_API_URL", "").rstrip("/")
BASE44_API_KEY = os.environ.get("BASE44_API_KEY", "")


def _headers():
    return {
        "api_key": BASE44_API_KEY,
        "Content-Type": "application/json",
    }


def list_rfps():
    """Return all RFPs in the database. Used for deduplication."""
    url = f"{BASE44_API_URL}/entities/RFP"
    r = requests.get(url, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def existing_solicitation_ids() -> set[str]:
    """Set of already-seen solicitation IDs for deduplication."""
    try:
        rfps = list_rfps()
        return {r["solicitation_id"] for r in rfps if r.get("solicitation_id")}
    except Exception as e:
        print(f"[base44] Failed to fetch existing RFPs: {e}")
        return set()


def create_rfp(data: dict) -> dict:
    """Insert a new RFP record."""
    url = f"{BASE44_API_URL}/entities/RFP"
    payload = {
        "solicitation_id": data.get("solicitation_id", ""),
        "title": data.get("title", "Untitled"),
        "agency": data.get("agency", ""),
        "description": data.get("description", ""),
        "contact_info": data.get("contact_info", ""),
        "deadline": data.get("deadline") or None,
        "prebid_date": data.get("prebid_date") or None,
        "source": data.get("source", ""),
        "url": data.get("url", ""),
        "ai_analysis": data.get("ai_analysis", ""),
        "relevance_score": data.get("relevance_score", 0),
        "relevance_reason": data.get("relevance_reason", ""),
        "category": data.get("category", "Other"),
        "scoring_source": data.get("scoring_source", "html"),
        "pdf_url": data.get("pdf_url"),
        "nigp_matches": data.get("nigp_matches", ""),
        "status": "New",
        "seen_date": data.get("seen_date"),
        "notified": False,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    r = requests.post(url, headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def create_job_log(data: dict) -> dict:
    """Insert a job log record."""
    url = f"{BASE44_API_URL}/entities/JobLog"
    r = requests.post(url, headers=_headers(), json=data, timeout=30)
    r.raise_for_status()
    return r.json()


def update_rfp(rfp_id: str, updates: dict) -> dict:
    """Update fields on an existing RFP record. Used by backfill."""
    url = f"{BASE44_API_URL}/entities/RFP/{rfp_id}"
    clean = {k: v for k, v in updates.items() if v is not None}
    r = requests.put(url, headers=_headers(), json=clean, timeout=30)
    r.raise_for_status()
    return r.json()
