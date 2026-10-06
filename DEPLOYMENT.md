# LeadPilot Production Deployment

## Required environment variables

Set these in your hosting provider. Never commit real secrets to GitHub.

```text
APP_SECRET=
APP_URL=https://your-production-domain.example
DATABASE_URL=
ALLOW_SIGNUP=true
DEMO_SEED=false
FREE_TRIAL_LEADS=0

YOUTUBE_API_KEY=
APIFY_API_TOKEN=
HUNTER_API_KEY=

STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_STARTER=price_1UNY6eKQRIyueFqxpuElWXQ4
STRIPE_PRICE_GROWTH=price_1UNY76KQRIyueFqxFshupsFb
STRIPE_PRICE_SCALE=price_1UNY7NKQRIyueFqxLqZP43HR
```

## Start command

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Stripe webhook

After the production URL exists, create a Stripe webhook endpoint:

```text
https://YOUR-DOMAIN/stripe/webhook
```

Subscribe it to:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

Copy the resulting signing secret (`whsec_...`) into `STRIPE_WEBHOOK_SECRET`.

## Database

Use PostgreSQL in production and set `DATABASE_URL` from the hosting provider.
The application performs a small compatibility migration for existing LeadPilot
workspace billing columns during startup.

## Security

Never commit:

- `.env`
- `.venv/`
- SQLite databases
- Stripe secret keys
- provider API keys
