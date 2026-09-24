# MySync

MySync is a Flask clipboard-sharing web app. It works locally with SQLite and can be deployed to Render with PostgreSQL.

## What this version adds

- Signup/login with an email address and a **MySync password**.
- The app never asks for or stores your Google/Gmail password.
- Every account gets an automatically generated public **MySync ID** like `MS-9F2A1C8D44B1`.
- Other users can add you with that ID.
- Each browser installation gets its own persistent device ID.
- Clipboard items are grouped by account and device.
- Each item can be shared with your contacts or kept private.
- Contacts only see items you marked as shared.
- Owners can unshare or delete their own items.
- Real-time refresh via Flask-SocketIO.
- SQLite locally; PostgreSQL on Render.
- Render Blueprint included in `render.yaml`.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

For phone/LAN access, find the Linux computer's LAN IP:

```bash
hostname -I
```

Then open `http://YOUR_LINUX_IP:8000` on the other device.

## GitHub

Do **not** commit `instance/mysync.db`, `.env`, or your virtual environment. `.gitignore` already excludes them.

```bash
git init
git add .
git commit -m "Initial MySync app"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/mysync.git
git push -u origin main
```

## Render

The included `render.yaml` creates:

- a Python web service
- a PostgreSQL database
- a generated `SECRET_KEY`
- `DATABASE_URL` wired automatically from the database
- `/healthz` as the health check

Connect the GitHub repository to Render and use **Blueprints** / the `render.yaml` configuration. Render's normal Flask build command is `pip install -r requirements.txt`; the production start command in this project uses Gunicorn with one worker and multiple threads for Flask-SocketIO.

### Important about the free database

The included blueprint uses Render's Free Postgres plan so you can test the deployment without paying. Render currently states that Free Postgres databases expire 30 days after creation; after the grace period, the database and its data are deleted unless you upgrade. For long-term use, change the database to a paid plan. The free web service also has Render's normal free-tier limits.

### Existing `mysync.db`

The old MySync v2 SQLite database is **not automatically uploaded or migrated** into this account-based version. That is intentional: the old records have no user owner, so assigning them to an account would be ambiguous. Start with the new database, or migrate old records manually if you need them.

## Notes

- Device IDs are browser-installation IDs stored in `localStorage`, not hardware IDs.
- Clearing browser site data or changing browsers creates a new device ID.
- Browser JavaScript cannot continuously read the operating system clipboard without user/browser permission. A native companion app can be added later for automatic clipboard monitoring.
- For a public deployment, use HTTPS (Render provides HTTPS) and keep `SECRET_KEY` private.
