# EMC RFP Agent — Railway Service

Python backend for the EMC RFP Tracker. Scrapes Texas RFP sources, analyzes them with Perplexity, and pushes results to Base44.

## Local testing

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in your keys in .env
python app.py
```

Then test:
```bash
# Health check
curl http://localhost:8080/health

# Trigger a run
curl -X POST http://localhost:8080/run \
  -H "Authorization: Bearer emc-railway-2026-momo" \
  -H "Content-Type: application/json" \
  -d '{"triggered_by": "manual"}'

# Check status
curl http://localhost:8080/status \
  -H "Authorization: Bearer emc-railway-2026-momo"
```

## Deploying to Railway

1. Push this folder to a new GitHub repo
2. Go to railway.app → New Project → Deploy from GitHub
3. Pick the repo
4. Add environment variables in Railway UI:
   - `BASE44_API_URL` = `https://base44.app/api/apps/6a205fdf0bd65bde5b8b84cf`
   - `BASE44_API_KEY` = your new Base44 key
   - `PERPLEXITY_API_KEY` = your Perplexity key
   - `RAILWAY_SECRET` = `emc-railway-2026-momo`
   - `SAM_API_KEY` = `DEMO_KEY` (or your own from sam.gov)
5. Railway auto-deploys. Note the public URL (e.g. `emc-rfp-agent-production.up.railway.app`)
6. Test the URL: `curl https://YOUR-URL.railway.app/health`

## After deploy — Base44 side

1. Open Base44 → paste the Base44 update prompt
2. Add Base44 env vars:
   - `RAILWAY_URL` = `https://YOUR-URL.railway.app`
   - `RAILWAY_SECRET` = `emc-railway-2026-momo`

## Architecture

```
Base44 "Run Now" button
        │
        ▼
Base44 backend function (proxy)
        │  POST /run + Bearer token
        ▼
Railway Flask service
        │
        ├─► sources/txsmartbuy.py  (Jina + Perplexity)
        ├─► sources/samgov.py      (direct API)
        ├─► sources/bidnet.py      (Jina + Perplexity)
        └─► sources/civcast.py     (Jina + Perplexity)
        │
        ▼
Base44 REST API (push RFPs + JobLog)
```

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask entry point, endpoints, scheduler |
| `agent.py` | Orchestrates fetch → save pipeline |
| `base44_client.py` | Wrapper for Base44 REST API |
| `perplexity_client.py` | Perplexity + Jina helpers |
| `sources/*.py` | One file per RFP source |

## Adding a new source

Create `sources/yoursource.py` with a `fetch() -> list[dict]` and `SOURCE_NAME` constant. Add it to `sources/__init__.py`. Done.
