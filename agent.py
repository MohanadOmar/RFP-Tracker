"""Orchestrates the full fetch → analyze → save pipeline.

Sources that support dedup-before-Perplexity (txsmartbuy) get existing_ids
passed in so they can skip already-saved RFPs before spending API calls.
Sources that don't support it (samgov, bidnet, civcast) fall back to the
old fetch() signature and dedup happens after extraction.
"""
import time
import inspect
from datetime import datetime, date
from sources import ALL_SOURCES
import base44_client


def format_ai_analysis(rfp: dict) -> str:
    parts = []
    if rfp.get("relevance_reason"):
        parts.append(rfp["relevance_reason"])
    reqs = rfp.get("requirements") or []
    if reqs:
        parts.append("Requirements:\n" + "\n".join(f"• {r}" for r in reqs))
    if rfp.get("description"):
        parts.append(f"Description:\n{rfp['description']}")
    return "\n\n".join(parts)


def _call_source_fetch(source_module, existing_ids: set[str]) -> list[dict]:
    """Call source.fetch() with or without existing_ids depending on signature."""
    sig = inspect.signature(source_module.fetch)
    if "existing_ids" in sig.parameters:
        return source_module.fetch(existing_ids=existing_ids)
    return source_module.fetch()


def run(triggered_by: str = "manual") -> dict:
    t0 = time.time()
    errors = []
    sources_checked = 0
    solicitations_found = 0
    new_rfps_saved = 0

    print(f"[agent] Starting run — triggered by {triggered_by}")
    existing_ids = base44_client.existing_solicitation_ids()
    print(f"[agent] {len(existing_ids)} existing RFPs in database")

    today = date.today().isoformat()

    for source_module in ALL_SOURCES:
        sources_checked += 1
        source_name = source_module.SOURCE_NAME
        print(f"[{source_name}] Fetching...")

        try:
            rfps = _call_source_fetch(source_module, existing_ids)
            print(f"[{source_name}] Got {len(rfps)} RFPs from source")
        except Exception as e:
            errors.append(f"[{source_name}] Fetch failed: {e}")
            print(f"[{source_name}] ERROR: {e}")
            continue

        solicitations_found += len(rfps)

        for rfp in rfps:
            sol_id = rfp.get("solicitation_id")
            if not sol_id or sol_id in existing_ids:
                # existing_ids is updated in-place by txsmartbuy.fetch(),
                # so this still works as a final safety net
                continue

            try:
                payload = {
                    "solicitation_id": sol_id,
                    "title": rfp.get("title", "Untitled"),
                    "agency": rfp.get("agency", ""),
                    "description": rfp.get("description", ""),
                    "contact_info": rfp.get("contact_info", ""),
                    "deadline": rfp.get("deadline"),
                    "prebid_date": rfp.get("prebid_date"),
                    "source": rfp.get("source", source_name),
                    "url": rfp.get("url", ""),
                    "ai_analysis": format_ai_analysis(rfp),
                    "relevance_score": rfp.get("relevance_score", 0),
                    "relevance_reason": rfp.get("relevance_reason", ""),
                    "seen_date": today,
                }
                base44_client.create_rfp(payload)
                existing_ids.add(sol_id)
                new_rfps_saved += 1
                print(f"[{source_name}] Saved: {payload['title'][:50]}")
            except Exception as e:
                errors.append(f"[{source_name}/{sol_id}] Save failed: {e}")
                print(f"[{source_name}] Save error: {e}")

    duration = int(time.time() - t0)

    try:
        base44_client.create_job_log({
            "run_date": datetime.utcnow().isoformat() + "Z",
            "sources_checked": sources_checked,
            "solicitations_found": solicitations_found,
            "new_rfps_saved": new_rfps_saved,
            "errors": "\n".join(errors) if errors else None,
            "duration_seconds": duration,
            "triggered_by": triggered_by,
        })
    except Exception as e:
        print(f"[agent] Failed to write JobLog: {e}")

    print(f"[agent] Done in {duration}s — {new_rfps_saved} new RFPs saved, {len(errors)} errors")

    return {
        "sources_checked": sources_checked,
        "solicitations_found": solicitations_found,
        "new_rfps_saved": new_rfps_saved,
        "duration_seconds": duration,
        "errors": errors,
        "triggered_by": triggered_by,
    }
