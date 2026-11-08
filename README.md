# Raindrop.io Git Backup

Automated tool to export bookmarks from [Raindrop.io](https://raindrop.io/) and push them to a Git repository on a schedule.

Backups are exported in standard **Netscape Bookmark HTML format** (`bookmarks.html`), supported for direct import by web browsers including Chrome, Firefox, Safari, Edge, Brave, Vivaldi, and Arc.

## Features

- **Standard browser format**: exports full bookmark trees to HTML with collection hierarchies preserved.
- **Scheduled execution**: lightweight cron daemon inside the container via `supercronic`.
- **Startup trigger**: optional immediate backup when container launches (`RUN_ON_STARTUP=true`).
- **Diff-aware commits**: skips commit and push if bookmarks have not changed since the last run.
- **Credential masking**: tokens and URLs with embedded passwords are sanitized in all logs.
- **Environment-based config**: configured entirely via environment variables or a `.env` file.

## Setup

### 1. Raindrop.io Authentication

Choose one of two authentication methods:

#### Option A: Test Token (recommended)
Test tokens do not expire and require no redirect server:
1. Open [Raindrop Integrations Settings](https://app.raindrop.io/settings/integrations).
2. Select your application under **For Developers** (or create one).
3. Scroll down and click **Create test token**.
4. Set the token in your `.env` file:
   ```env
   RAINDROP_TOKEN=your_test_token
   ```

#### Option B: OAuth Flow
To use OAuth credentials with refresh tokens:
1. Set `RAINDROP_CLIENT_ID` and `RAINDROP_CLIENT_SECRET` in `.env`.
2. Run the interactive authorization helper:
   ```bash
   python main.py --auth
   ```
3. Copy the returned `RAINDROP_REFRESH_TOKEN` to your `.env` file. The application refreshes access tokens automatically when they expire.

### 2. Git Repository Setup

1. Create a repository for your backups (recommended: private).
2. Generate a Personal Access Token with write access (`repo` scope for GitHub).
3. Add the repository details to `.env`:
   ```env
   GIT_REMOTE_URL=https://github.com/username/bookmarks-backup.git
   GIT_TOKEN=ghp_your_github_token
   GIT_BRANCH=main
   ```

### 3. Running with Docker Compose

1. Copy the example configuration and adjust values:
   ```bash
   cp .env.example .env
   ```

2. Start the production container (uses published image):
   ```bash
   docker compose up -d
   ```

   Or run in development mode (builds locally with live source mounting):
   ```bash
   docker compose -f docker-compose.dev.yml up -d --build
   ```

3. View logs:
   ```bash
   docker compose logs -f
   # Or for development:
   docker compose -f docker-compose.dev.yml logs -f
   ```

### 4. Running Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your credentials

python main.py
```

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `RAINDROP_TOKEN` | - | Test token or access token from Raindrop.io |
| `RAINDROP_CLIENT_ID` | `6af0d98918593f567f2e2e47` | Raindrop application Client ID |
| `RAINDROP_CLIENT_SECRET` | - | Raindrop application Client Secret |
| `RAINDROP_REFRESH_TOKEN` | - | OAuth refresh token |
| `RAINDROP_COLLECTION_ID` | `0` | Collection ID to export (`0` for all bookmarks) |
| `GIT_REMOTE_URL` | - | Remote repository URL (HTTPS or SSH) |
| `GIT_TOKEN` | - | Personal access token for remote Git host |
| `GIT_USERNAME` | `oauth2` | HTTPS authentication username |
| `GIT_BRANCH` | `main` | Target branch |
| `GIT_USER_NAME` | `Raindrop Backup Bot` | Git author name |
| `GIT_USER_EMAIL` | `backup-bot@users.noreply.github.com` | Git author email |
| `GIT_COMMIT_MESSAGE` | `Backup Raindrop bookmarks: {date}` | Commit message format string |
| `BACKUP_FILE_NAME` | `bookmarks.html` | Export filename |
| `CRON_SCHEDULE` | `0 3 * * *` | Supercronic schedule expression (default: 03:00 daily) |
| `RUN_ON_STARTUP` | `true` | Run an export cycle immediately when container starts |
| `DATA_DIR` | `/app/data` | Working directory for cache and git checkout |
| `TZ` | `Europe/Amsterdam` | Container timezone |

## Browser Import

To restore or import `bookmarks.html`:
- **Chrome / Brave / Edge**: Open Bookmarks Manager (`Ctrl+Shift+O` or `Cmd+Option+B`), open the menu in the top-right corner, and select **Import bookmarks**.
- **Firefox**: Open Library (`Ctrl+Shift+O`), select **Import and Backup**, then **Import Bookmarks from HTML...**.
- **Safari**: Choose **File** -> **Import From** -> **Bookmarks HTML File...**.

