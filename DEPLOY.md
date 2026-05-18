# Railway Deployment Guide

Quick walkthrough to deploy this MVP on Railway with Postgres in ~5 minutes.

## What you need

1. A [Railway](https://railway.com/) account (Hobby plan, $5/mo)
2. Your repo on GitHub (this one)
3. **One** LLM API key:
   - `ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com/) (~$0.001/signal), or
   - `DEEPSEEK_API_KEY` from [platform.deepseek.com](https://platform.deepseek.com/) (~3-4× cheaper)
4. At least one of:
   - **Bot** token from [@BotFather](https://t.me/BotFather) + your Telegram user ID
   - **Telethon** API credentials (api_id + api_hash from https://my.telegram.org)

## Step-by-step

### 1. Create the project

1. Go to [railway.com/new](https://railway.com/new)
2. Click **"Deploy from GitHub repo"**
3. Select `claude_cryptosignalbot`
4. Railway detects the `Dockerfile` and starts building automatically

⚠️ The first build will fail because no env vars are set yet. That's expected.

### 2. Add Postgres database

In the same project:
1. Click **"+ New"** → **"Database"** → **"Add PostgreSQL"**
2. Wait ~30 sec until Postgres is provisioned
3. The DB is now provisioned with a `DATABASE_URL` variable available

### 3. Link DATABASE_URL to your app

1. Go to your **app service** (not the Postgres one)
2. **Variables** tab → **"+ New Variable"** → **"Add Reference"**
3. Select **Postgres** → **DATABASE_URL**
4. Click **Add**

Your app now automatically gets the Postgres connection string.

### 4. Add the rest of the env vars

In the app service → **Variables** → **"Raw Editor"**, paste:

```env
# Required: one LLM provider key (auto-detected; set both if you want to switch)
ANTHROPIC_API_KEY=sk-ant-...
# OR
DEEPSEEK_API_KEY=sk-...
# Optional: pin the provider when both keys are set
# LLM_PROVIDER=anthropic   # or "deepseek" or "auto" (default)

# Pick one (or both)

# --- Option A: bot (easiest — forward signals to bot)
BOT_ENABLED=true
BOT_TOKEN=123456:ABC-...
BOT_ALLOWED_USERS=123456789

# --- Option B: telethon (reads channels automatically)
TELETHON_ENABLED=true
TELETHON_API_ID=12345
TELETHON_API_HASH=abc...
TELETHON_PHONE=+1234567890
TELETHON_SESSION_STRING=<generate-via-step-5>
TELETHON_CHANNELS=daytrader_signals,binance_killers_official
```

### 5. (Telethon only) Generate session string

Skip if you only use the bot.

In the app service → **Settings** → **Service Shell**:

```bash
python scripts/get_telethon_session.py
```

Enter your API_ID, API_HASH, phone, SMS code. Copy the printed `SESSION_STRING`.

Add it to Variables: `TELETHON_SESSION_STRING=<paste>`.

### 6. Deploy

Either:
- Wait for auto-redeploy (Railway triggers on env var change)
- Or click **"Deploy"** on the service

### 7. Open the dashboard

In the app service → **Settings** → **Networking** → **"Generate Domain"**.

Railway gives you a public URL like `https://your-app.up.railway.app`. Open it.

You should see the dashboard. Forward a signal to your bot (or wait for Telethon to pick one up) — it'll show up within seconds.

## Verifying things work

Check **Logs** tab on the app service. You should see:

```
[info] startup.complete exchange=binance market=futures telethon=False bot=True web=http://0.0.0.0:8080 db=...
[info] binance.ws.connected market=futures
[info] tracker.started symbols=[]
[info] bot.started allowed_users=[123456789]
```

Forward a signal → expect:
```
[info] ingest.persisted signal_id=1 channel_id=1 symbol=BTCUSDT side=LONG entry=(...)
[info] tracker.registered signal_id=1 symbol=BTCUSDT
```

## Cost estimate

| Item | $/month |
|---|---|
| Hobby plan base | $5 (incl. $5 of usage credits) |
| App container (~50MB RAM, mostly idle) | $3–7 |
| Postgres (small) | $5–10 |
| **Total** | **~$13–22/month** |

## Updates

Just push to GitHub. Railway auto-redeploys on every push to the connected branch.

## Troubleshooting

**App crashes on startup**: check Logs. Common causes:
- `missing.llm_api_key` → add `ANTHROPIC_API_KEY` or `DEEPSEEK_API_KEY`
- `no_source_enabled` → enable `BOT_ENABLED` or `TELETHON_ENABLED`

**Postgres connection fails**: verify the `DATABASE_URL` variable is referenced
correctly. The URL should start with `postgresql://` (we auto-convert to
asyncpg driver).

**Health check timing out**: increase `healthcheckTimeout` in `railway.json`.
Default is 60s which should be enough.

**Telethon "401 Unauthorized"**: SESSION_STRING expired or was created for a
different api_id/api_hash. Regenerate via `scripts/get_telethon_session.py`.
