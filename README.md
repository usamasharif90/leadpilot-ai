# LeadPilot AI — Sellable v1

This package is the commercial SaaS version of the coach lead system.

## Included

- Professional multi-page SaaS UI
- Signup / login / logout
- Password hashing + signed session cookies
- Workspace isolation
- Per-workspace lead database
- Plans + monthly lead limits
- Stripe Checkout hooks
- PostgreSQL support
- SQLite fallback for local development
- Lead Finder
- Lead pipeline board
- Saved searches
- Lead lists
- Clients
- Weekly batches
- Outreach drafts
- Provider status
- Background discovery jobs
- CSV export
- Demo workspace + demo data
- Dockerfile + Docker Compose
- Existing YouTube / Instagram / Hunter pipeline preserved under `/engine`

## Demo login

`demo@leadpilot.local`
`demo1234`

Change/remove this before public launch by setting `DEMO_SEED=false` after creating your own owner account.

## Run locally

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Run with Docker + Postgres

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000`.

## Connect live lead discovery

Set:

```bash
YOUTUBE_API_KEY=...
```

Then open **Data Sources → Run discovery**.

The app invokes the included `engine/youtube_lead_finder.py` in a background job and imports newly qualified channels into the logged-in workspace.

If no YouTube key is configured, the job is marked `needs_credentials`; the system does not pretend demo data came from live APIs.

## Instagram and email enrichment

The original enrichment scripts are included in `/engine`.

Set:

```bash
IG_ACCESS_TOKEN=...
IG_USER_ID=...
HUNTER_API_KEY=...
```

The UI exposes provider status. Automatic chained enrichment can be added to the same job runner when you choose to pay for those providers.

## Stripe

Set:

```bash
STRIPE_SECRET_KEY=
STRIPE_PRICE_STARTER=
STRIPE_PRICE_GROWTH=
STRIPE_PRICE_SCALE=
```

Then the Billing page creates Stripe Checkout sessions.

`Use demo plan` is included for testing and should be removed/disabled before public launch.

## Production launch checklist

Before taking self-service customers:

1. Set a long random `APP_SECRET`.
2. Set `DEMO_SEED=false`.
3. Use PostgreSQL.
4. Configure HTTPS and a real domain.
5. Configure Stripe + real price IDs.
6. Configure YouTube/Hunter/Meta credentials.
7. Turn `ALLOW_SIGNUP` on/off according to your sales process.
8. Put provider secrets in your host's environment/secret manager.
9. Add backups/monitoring on your host.
10. Replace example/demo leads with live discovery for customer workspaces.

## Recommended first sales workflow

For your first agencies, keep onboarding controlled:
- create the customer's workspace
- set their plan
- configure their ICP
- run live discovery
- review the first batch manually
- deliver/export
- only then open self-service access broadly

This reduces risk while you validate data quality and provider economics.
