"""Flask service: /run (manual trigger) + /health + daily scheduler."""
import os
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

import agent

app = Flask(__name__)

RAILWAY_SECRET = os.environ.get("RAILWAY_SECRET", "")
SCHEDULER_HOUR_CST = int(os.environ.get("SCHEDULER_HOUR_CST", 9))

last_run_status = {"status": "idle", "result": None}


def _check_auth() -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth.replace("Bearer ", "").strip() == RAILWAY_SECRET


def _run_in_background(triggered_by: str):
    global last_run_status
    last_run_status = {"status": "running", "started_at": datetime.utcnow().isoformat()}
    try:
        result = agent.run(triggered_by=triggered_by)
        last_run_status = {"status": "completed", "result": result, "finished_at": datetime.utcnow().isoformat()}
    except Exception as e:
        last_run_status = {"status": "failed", "error": str(e), "finished_at": datetime.utcnow().isoformat()}
        print(f"[scheduler] Job failed: {e}")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "emc-rfp-agent",
        "last_run": last_run_status,
    })


@app.route("/run", methods=["POST"])
def trigger_run():
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    triggered_by = body.get("triggered_by", "manual")

    if last_run_status.get("status") == "running":
        return jsonify({
            "status": "already_running",
            "message": "A job is already in progress",
            "started_at": last_run_status.get("started_at"),
        }), 202

    thread = threading.Thread(target=_run_in_background, args=(triggered_by,), daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": "Agent run started in background",
        "triggered_by": triggered_by,
    }), 202


@app.route("/status", methods=["GET"])
def status():
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(last_run_status)


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "EMC RFP Agent", "status": "online"})


# Daily scheduler — 9 AM CST = 14:00 UTC (15:00 during DST)
def scheduled_run():
    print(f"[scheduler] Cron trigger at {datetime.utcnow().isoformat()}Z")
    _run_in_background("scheduled")


scheduler = BackgroundScheduler(timezone="America/Chicago")
scheduler.add_job(
    scheduled_run,
    trigger="cron",
    hour=SCHEDULER_HOUR_CST,
    minute=0,
    id="daily_rfp_fetch",
    replace_existing=True,
)
scheduler.start()
print(f"[scheduler] Daily run scheduled for {SCHEDULER_HOUR_CST}:00 CST")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
