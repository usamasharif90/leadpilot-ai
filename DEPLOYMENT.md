# LeadPilot deployment

GitHub stores the source code; it does not run this FastAPI app by itself.

Recommended production setup:
1. Push this repository to GitHub.
2. Deploy the GitHub repo to Render, Railway, Fly.io, or another Python web host.
3. Use PostgreSQL in production by setting `DATABASE_URL`.
4. Add secrets in the host's Environment Variables UI:
   - APP_SECRET
   - YOUTUBE_API_KEY
   - APIFY_API_TOKEN
   - HUNTER_API_KEY
   - DATABASE_URL
   - Stripe keys if billing is enabled
5. Never commit `.env`, `.venv`, or `data/leadpilot.db`.

Start command:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`
