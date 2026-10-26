# Between the Covers — Book Club

A beginner-friendly, server-rendered book club app built with FastAPI, Jinja, SQLAlchemy, and SQLite. Members sign in with Google, register a profile, recommend books, vote, and view ranked results.

## Current behavior

- Google sign-in; first-time members complete their name and favorite genre.
- Admins can manage the genre list and voting schedule from the admin page, and close an open voting period early. A manually closed period reopens at its scheduled end. Initial defaults are Fiction, Non-Fiction, Mystery, Sci-Fi, and Fantasy, with two-day voting periods.
- Dates and times are shown in UTC and the viewer's browser-local time.
- Admins enter the first voting start in their browser's local time; the app stores it as UTC.
- Each member has one current recommendation. Replacing it archives the previous recommendation and its votes.
- Archived recommendations and votes remain in the member's history and do not count in current totals.
- Members may vote for their own recommendation, have only one active vote, and can change it during an open voting period.
- Voting opens every 2 days at 00:00 UTC, anchored at October 25, 2026. Each period lasts 2 days.
- Results are ordered by active vote total and refreshed in the open page every 10 seconds.
- Registration, recommendation, and vote actions send confirmations over SMTP. A delivery failure does not undo the action and is recorded in the database.

## Set up and run locally (Windows PowerShell)

### 1. Install prerequisites

- Install Python 3.11 or newer and ensure the Python launcher or `python` command is available in PowerShell.
- Install Git if you plan to clone the project.
- Google and SMTP credentials are optional for starting the site, but required for real sign-in and email delivery.

Open PowerShell in the project root (the folder containing `requirements.txt`), then create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If PowerShell blocks virtual-environment activation, use this for the current terminal only, then activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 2. Create local configuration

Copy the example environment file and edit it:

```powershell
Copy-Item .env.example .env
```

At minimum, change `SESSION_SECRET` in `.env` to a long random value. Keep `.env` private; it is excluded from Git. Leave `COOKIE_SECURE=false` for local HTTP development. Use `true` only when the site is served over HTTPS.

Set `ADMIN_EMAILS` to the comma-separated Google email addresses that should have admin access. Sign in and complete member registration with one of those addresses; the **Admin** link will then appear. The admin page saves voting dates and genres in the database. `VOTING_FIRST_START`, `VOTING_PERIOD_DAYS`, and `DEFAULT_GENRES` provide initial values when the database is created.

### 3. Configure Google sign-in (optional for initial startup)

In Google Cloud Console, create or select a project, configure the OAuth consent screen, and create an OAuth client with application type **Web application**. Add this authorized redirect URI for local development:

`http://localhost:8000/auth/callback`

Put the resulting client ID and client secret in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`. Keep the redirect URI in `GOOGLE_REDIRECT_URI` aligned with the Google Console setting. Google sign-in will not work until these values are configured.

### 4. Configure confirmation email (optional for initial startup)

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM` in `.env` using the values from your email provider. Do not commit or share the real password. With SMTP unset, the app still completes registration, recommendation, and vote actions, but records the email failure and tells the member.

### 5. Start the app

From the project root, with the virtual environment active:

```powershell
python -m uvicorn app.main:app --reload
```

Open <http://localhost:8000>. Stop the development server with Ctrl+C in PowerShell.

The local SQLite database is created at `data/bookclub.db` and the `data` folder is ignored by Git. The first run creates tables automatically. Before changing a deployed database schema, introduce and use Alembic migrations.

## Run with Docker

Install Docker Desktop, copy `.env.example` to `.env`, and configure the session secret plus any Google/SMTP settings. From the project root, run:

```powershell
docker compose up --build
```

Visit <http://localhost:8000>. Stop containers with Ctrl+C, or run `docker compose down`. The named `bookclub-data` volume preserves the SQLite database across container restarts. The `.env` file is runtime configuration and is not copied into the image.

## Deployment notes

For a deployed app that needs concurrent writes, configure `DATABASE_URL` for PostgreSQL (supported by the included PostgreSQL driver), set `COOKIE_SECURE=true`, use HTTPS, configure the production Google callback URL, and keep secrets in the hosting provider's secret store. Back up the database. Do not use the development session secret in production. The app currently creates tables automatically; add Alembic migrations before managing a production schema over time.

## Tests

With the virtual environment active, run these commands from the project root:

```powershell
python -m pytest -q
```

Tests cover the voting schedule boundaries, vote-count ordering, archived-vote behavior, email failure recording, and the active-vote database constraint.

## Later improvements (not needed for the first version)

- Email resend or automated retry tools.
- Additional sign-in providers, live updates via WebSockets, and a PostgreSQL deployment.
