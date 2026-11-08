import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("raindrop-backup")

RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"
RAINDROP_OAUTH_TOKEN_URL = "https://raindrop.io/oauth/access_token"
RAINDROP_OAUTH_AUTH_URL = "https://raindrop.io/oauth/authorize"


def mask_secret(url_or_string: str) -> str:
    """Mask tokens and passwords in URLs or text strings for safe logging."""
    return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", url_or_string)


def run_git_cmd(args: list[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Execute git command with masked logging on errors."""
    cmd_repr = "git " + " ".join(mask_secret(arg) for arg in args)
    logger.debug("Executing: %s", cmd_repr)

    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )

    if check and result.returncode != 0:
        err_msg = mask_secret(result.stderr.strip() or result.stdout.strip())
        raise RuntimeError(f"Git command failed ({cmd_repr}): {err_msg}")

    return result


class RaindropClient:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.token_file = data_dir / "raindrop_token.json"

        self.token = os.getenv("RAINDROP_TOKEN")
        self.client_id = os.getenv("RAINDROP_CLIENT_ID")
        self.client_secret = os.getenv("RAINDROP_CLIENT_SECRET")
        self.refresh_token = os.getenv("RAINDROP_REFRESH_TOKEN")

        self._load_cached_tokens()

    def _load_cached_tokens(self) -> None:
        if not self.token_file.exists():
            return

        try:
            with open(self.token_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("access_token"):
                    self.token = cached["access_token"]
                if cached.get("refresh_token"):
                    self.refresh_token = cached["refresh_token"]
        except Exception as e:
            logger.warning("Failed to read token cache: %s", e)

    def _save_cached_tokens(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        self.token = access_token
        if refresh_token:
            self.refresh_token = refresh_token

        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "access_token": self.token,
                "refresh_token": self.refresh_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.chmod(self.token_file, 0o600)
        except Exception as e:
            logger.warning("Failed to save updated tokens to cache: %s", e)

    def refresh_access_token(self) -> bool:
        """Refresh expired access token using Raindrop OAuth refresh flow."""
        if not (self.client_id and self.client_secret and self.refresh_token):
            return False

        logger.info("Attempting to refresh Raindrop access token...")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }

        try:
            resp = requests.post(RAINDROP_OAUTH_TOKEN_URL, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                new_access = data.get("access_token")
                new_refresh = data.get("refresh_token", self.refresh_token)
                if new_access:
                    self._save_cached_tokens(new_access, new_refresh)
                    logger.info("Raindrop access token refreshed successfully.")
                    return True
            logger.error("Token refresh failed: HTTP %s - %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Token refresh request encountered an error: %s", e)

        return False

    def export_bookmarks_html(self, collection_id: int = 0) -> str:
        """Download bookmarks in standard Netscape HTML format."""
        if not self.token:
            if not self.refresh_access_token():
                raise ValueError(
                    "No Raindrop token available. Provide RAINDROP_TOKEN (test token) "
                    "or RAINDROP_CLIENT_ID, RAINDROP_CLIENT_SECRET, and RAINDROP_REFRESH_TOKEN."
                )

        url = f"{RAINDROP_API_BASE}/raindrops/{collection_id}/export.html"
        headers = {"Authorization": f"Bearer {self.token}"}

        resp = requests.get(url, headers=headers, timeout=60)

        # Handle token expiration (HTTP 401)
        if resp.status_code == 401 and self.refresh_access_token():
            headers = {"Authorization": f"Bearer {self.token}"}
            resp = requests.get(url, headers=headers, timeout=60)

        if resp.status_code != 200:
            raise RuntimeError(f"Raindrop export failed with status {resp.status_code}: {resp.text}")

        content = resp.text
        if "<!DOCTYPE" not in content and "NETSCAPE-Bookmark-file-1" not in content and "<DL>" not in content:
            raise ValueError("Downloaded content is not a valid Netscape bookmark HTML document.")

        return content


class GitRepositoryManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.repo_dir = data_dir / "repo"

        self.remote_url = os.getenv("GIT_REMOTE_URL")
        self.token = os.getenv("GIT_TOKEN")
        self.username = os.getenv("GIT_USERNAME", "oauth2")
        self.branch = os.getenv("GIT_BRANCH", "main")
        self.user_name = os.getenv("GIT_USER_NAME", "Raindrop Backup Bot")
        self.user_email = os.getenv("GIT_USER_EMAIL", "backup-bot@users.noreply.github.com")

        if not self.remote_url:
            raise ValueError("Environment variable GIT_REMOTE_URL is required.")

        self.authenticated_url = self._build_authenticated_url(self.remote_url, self.token, self.username)

    @staticmethod
    def _build_authenticated_url(url: str, token: Optional[str], username: str) -> str:
        """Inject credentials into HTTPS remote URL if token is provided."""
        if not token or not url.startswith("https://"):
            return url

        parsed = urlparse(url)
        # Avoid overriding existing inline auth
        if parsed.username or parsed.password:
            return url

        netloc = f"{username}:{token}@{parsed.netloc}"
        return urlunparse(parsed._replace(netloc=netloc))

    def setup_repository(self) -> None:
        """Clone or sync local working repository."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Allow git commands in container environment without ownership conflicts
        subprocess.run(
            ["git", "config", "--global", "--add", "safe.directory", "*"],
            check=False,
            capture_output=True,
        )

        git_dir = self.repo_dir / ".git"
        if not git_dir.exists():
            if self.repo_dir.exists():
                shutil.rmtree(self.repo_dir)

            logger.info("Cloning repository from %s into %s...", mask_secret(self.remote_url), self.repo_dir)
            clone_res = run_git_cmd(["clone", self.authenticated_url, str(self.repo_dir)], check=False)

            if clone_res.returncode != 0:
                logger.info("Remote clone failed (empty repository?). Initializing new repo...")
                self.repo_dir.mkdir(parents=True, exist_ok=True)
                run_git_cmd(["init", "-b", self.branch], cwd=self.repo_dir)
                run_git_cmd(["remote", "add", "origin", self.authenticated_url], cwd=self.repo_dir)
            else:
                run_git_cmd(["checkout", self.branch], cwd=self.repo_dir, check=False)
        else:
            run_git_cmd(["remote", "set-url", "origin", self.authenticated_url], cwd=self.repo_dir)
            logger.info("Fetching latest changes from origin...")
            run_git_cmd(["fetch", "origin"], cwd=self.repo_dir, check=False)
            run_git_cmd(["checkout", self.branch], cwd=self.repo_dir, check=False)
            run_git_cmd(["pull", "origin", self.branch], cwd=self.repo_dir, check=False)

        run_git_cmd(["config", "user.name", self.user_name], cwd=self.repo_dir)
        run_git_cmd(["config", "user.email", self.user_email], cwd=self.repo_dir)

    def commit_and_push(self, changed_files: list[str], commit_message: str) -> bool:
        """Stage files, commit, and push to origin if changes exist."""
        status = run_git_cmd(["status", "--porcelain"], cwd=self.repo_dir).stdout.strip()
        if not status:
            logger.info("No changes detected in bookmarks. Nothing to commit.")
            return False

        for file_path in changed_files:
            run_git_cmd(["add", file_path], cwd=self.repo_dir)

        diff = run_git_cmd(["diff", "--cached", "--quiet"], cwd=self.repo_dir, check=False)
        if diff.returncode == 0:
            logger.info("Staged files have no diffs. Skipping commit.")
            return False

        logger.info("Committing changes: %s", commit_message)
        run_git_cmd(["commit", "-m", commit_message], cwd=self.repo_dir)

        logger.info("Pushing changes to origin %s...", self.branch)
        run_git_cmd(["push", "-u", "origin", self.branch], cwd=self.repo_dir)
        logger.info("Successfully pushed backup to %s.", mask_secret(self.remote_url))
        return True


def perform_backup() -> None:
    data_dir = Path(os.getenv("DATA_DIR", "/app/data")).resolve()
    collection_id = int(os.getenv("RAINDROP_COLLECTION_ID", "0"))
    backup_filename = os.getenv("BACKUP_FILE_NAME", "bookmarks.html")

    logger.info("Starting Raindrop bookmark backup...")
    client = RaindropClient(data_dir=data_dir)
    html_content = client.export_bookmarks_html(collection_id=collection_id)
    logger.info("Exported HTML received (%d bytes).", len(html_content))

    repo_mgr = GitRepositoryManager(data_dir=data_dir)
    repo_mgr.setup_repository()

    dest_file = repo_mgr.repo_dir / backup_filename
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    custom_msg = os.getenv("GIT_COMMIT_MESSAGE", "Backup Raindrop bookmarks: {date}")
    commit_msg = custom_msg.format(date=now_iso)

    repo_mgr.commit_and_push([backup_filename], commit_msg)
    logger.info("Backup procedure completed successfully.")


def run_oauth_flow() -> None:
    """Helper CLI mode to obtain access and refresh tokens interactively."""
    client_id = os.getenv("RAINDROP_CLIENT_ID") or input("Enter Raindrop Client ID: ").strip()
    client_secret = os.getenv("RAINDROP_CLIENT_SECRET") or input("Enter Raindrop Client Secret: ").strip()
    redirect_uri = os.getenv("RAINDROP_REDIRECT_URI", "urn:ietf:wg:oauth:2.0:oob")

    auth_url = (
        f"{RAINDROP_OAUTH_AUTH_URL}?client_id={client_id}&redirect_uri={redirect_uri}"
    )
    print("\n1. Open this URL in your browser and authorize the app:")
    print(f"   {auth_url}\n")
    code = input("2. Enter the authorization code: ").strip()

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
    }

    resp = requests.post(RAINDROP_OAUTH_TOKEN_URL, json=payload, timeout=30)
    if resp.status_code == 200:
        data = resp.json()
        print("\nTokens received successfully!")
        print(f"RAINDROP_TOKEN={data.get('access_token')}")
        print(f"RAINDROP_REFRESH_TOKEN={data.get('refresh_token')}")
        print(f"Expires in: {data.get('expires')} seconds")
    else:
        print(f"\nFailed to obtain tokens (HTTP {resp.status_code}): {resp.text}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup Raindrop.io bookmarks to Git")
    parser.add_argument("--auth", action="store_true", help="Run interactive OAuth authorization")
    args = parser.parse_args()

    if args.auth:
        run_oauth_flow()
        return

    try:
        perform_backup()
    except Exception as exc:
        logger.error("Backup failed: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
