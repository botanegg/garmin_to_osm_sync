"""
Garmin Connect -> OpenStreetMap GPX sync (OAuth2)

This version uses OSM OAuth2 (interactive browser flow) to obtain an access token
and refresh token. Tokens are stored locally in `tokens.json` so you don't need to
re-authorize on every run. If a token is missing or expired the script will open
a browser to authorize and then exchange the code for tokens.

.env variables expected (examples):

# Garmin
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your_garmin_password

# OSM OAuth2 (register your app at https://www.openstreetmap.org/oauth2/clients)
OSM_CLIENT_ID=your_client_id
OSM_CLIENT_SECRET=your_client_secret
REDIRECT_URI=http://127.0.0.1:8080/callback
OSM_USERNAME=your_osm_username

# Optional
DOWNLOAD_DIR=downloads
PROCESSED_ACTIVITIES_FILE=processed_ids.txt
MAX_ACTIVITIES=10
DRY_RUN=false

# Recommended pip packages:
# pip install garminconnect requests python-dotenv requests-oauthlib

"""

import os
import json
import logging
import time
import webbrowser
import argparse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs
from datetime import datetime, timedelta, timezone

import requests
import sqlite3
from dotenv import load_dotenv

# Garmin library imports
try:
    from garminconnect import (
        Garmin,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
        GarminConnectAuthenticationError,
    )
except Exception:
    raise

# --- CONFIGURATION ---
load_dotenv()

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
CLIENT_ID = os.getenv("OSM_CLIENT_ID")
CLIENT_SECRET = os.getenv("OSM_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
OSM_USERNAME = os.getenv("OSM_USERNAME")

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads"))
PROCESSED_ACTIVITIES_FILE = Path(os.getenv("PROCESSED_ACTIVITIES_FILE", "processed_ids.txt"))
MAX_ACTIVITIES = int(os.getenv("MAX_ACTIVITIES", "10"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")

TOKENS_FILE = Path("tokens.json")

OSM_BASE = "https://www.openstreetmap.org"
AUTHORIZATION_URL = f"{OSM_BASE}/oauth2/authorize"
TOKEN_URL = f"{OSM_BASE}/oauth2/token"
UPLOAD_URL = f"{OSM_BASE}/api/0.6/gpx/create"
SCOPES = "read_gpx write_gpx"

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("garmin_osm_sync_oauth2")

# --- OAUTH2 LOCAL CALLBACK ---
authorization_code = None

class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global authorization_code
        # send a friendly HTML page
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        html = ("<html><body style='font-family:sans-serif; padding:2rem;'>"
                "<h2>Authorization complete</h2><p>You can close this tab and return to the application.</p>"
                "</body></html>")
        self.wfile.write(html.encode("utf-8"))

        # parse code
        if "?" in self.path:
            qs = parse_qs(self.path.split("?", 1)[1])
            code = qs.get("code", [None])[0]
            if code:
                authorization_code = code
                logger.info("Received authorization code via callback")
            else:
                logger.error("Callback received but no code present: %s", self.path)


def get_authorization_code():
    """Open browser to authorize and run a temporary local server to receive code."""
    global authorization_code
    authorization_code = None

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
    }
    url = f"{AUTHORIZATION_URL}?{urlencode(params)}"
    logger.info("Opening browser for OSM authorization: %s", url)
    webbrowser.open(url)

    # start local server
    parsed = REDIRECT_URI
    # expecting http://127.0.0.1:8080/... so extract host/port
    from urllib.parse import urlparse
    p = urlparse(REDIRECT_URI)
    host = p.hostname or "127.0.0.1"
    port = p.port or 8080

    httpd = HTTPServer((host, port), OAuthCallbackHandler)
    logger.info("Waiting for authorization callback on %s:%s...", host, port)
    httpd.handle_request()  # handle a single request then exit
    httpd.server_close()

    return authorization_code

# --- TOKEN STORAGE & EXCHANGE ---

def load_tokens():
    if not TOKENS_FILE.exists():
        return None
    try:
        data = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        # Expect structure: {access_token, refresh_token, expires_at (iso)}
        return data
    except Exception:
        logger.exception("Failed to load tokens.json")
        return None


def save_tokens(data: dict):
    try:
        TOKENS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Saved tokens to %s", TOKENS_FILE)
    except Exception:
        logger.exception("Failed to save tokens.json")


def exchange_code_for_tokens(code: str):
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
    resp.raise_for_status()
    j = resp.json()

    # compute expiry time
    expires_in = j.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()

    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token"),
        "expires_at": expires_at,
        "scope": j.get("scope"),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    }
    save_tokens(tokens)
    return tokens


def refresh_tokens(refresh_token: str):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    resp = requests.post(TOKEN_URL, data=payload, timeout=30)
    resp.raise_for_status()
    j = resp.json()

    expires_in = j.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()

    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token", refresh_token),
        "expires_at": expires_at,
        "scope": j.get("scope"),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    }
    save_tokens(tokens)
    return tokens


def ensure_access_token():
    """Load tokens.json or run authorization flow. Refresh if expired.
    Returns the access_token string.
    """
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        raise RuntimeError("Missing OSM_CLIENT_ID / OSM_CLIENT_SECRET / REDIRECT_URI in environment")

    tokens = load_tokens()
    if tokens is None:
        # run interactive authorization
        code = get_authorization_code()
        if not code:
            raise RuntimeError("Failed to obtain authorization code from callback")
        tokens = exchange_code_for_tokens(code)
        return tokens["access_token"]

    # if expires_at present, check
    expires_at = tokens.get("expires_at")
    if expires_at:
        expires_dt = datetime.fromisoformat(expires_at)
        if datetime.now(timezone.utc) + timedelta(seconds=60) >= expires_dt:
            logger.info("Access token expired or near expiry, refreshing using refresh_token")
            tokens = refresh_tokens(tokens.get("refresh_token"))
            return tokens.get("access_token")

    return tokens.get("access_token")

# --- GPX UPLOAD (bearer token) ---

def upload_gpx_with_bearer(access_token: str, gpx_filepath: Path, description: str, tags: str, visibility: str):
    headers = {"Authorization": f"Bearer {access_token}"}
    with gpx_filepath.open("rb") as f:
        files = {"file": (gpx_filepath.name, f, "application/gpx+xml")}
        data = {"description": description, "tags": tags, "visibility": visibility}
        resp = requests.post(UPLOAD_URL, files=files, data=data, headers=headers, timeout=60)
    return resp

# --- File helpers ---

DB_FILE = Path(os.getenv("DB_FILE", "data.db"))

def init_db():
    """Create the processed_activities table if it doesn't exist."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_activities (
            activity_id TEXT PRIMARY KEY,
            uploaded_at TEXT,
            gpx_id TEXT,
            status TEXT,
            metadata TEXT
        )
        """)
        conn.commit()
    except Exception:
        logger.exception("Failed to initialize DB")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_processed_ids():
    """Return a set of processed activity IDs from the SQLite DB."""
    try:
        init_db()
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT activity_id FROM processed_activities")
        rows = cur.fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        logger.exception("Failed to read processed ids from DB")
        return set()


def add_processed_id(activity_id, gpx_id=None, status='uploaded', metadata=None):
    """Insert or update a processed activity into the DB.

    Keeps track of when it was uploaded, optional gpx_id and arbitrary metadata (JSON).
    """
    try:
        init_db()
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO processed_activities (activity_id, uploaded_at, gpx_id, status, metadata) VALUES (?, ?, ?, ?, ?)",
            (str(activity_id), datetime.now(timezone.utc).isoformat(), gpx_id, status, json.dumps(metadata) if metadata else None),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("Failed to insert processed id into DB")

# --- MAIN ---


def main():
    parser = argparse.ArgumentParser(description="Garmin Connect -> OSM GPX sync")
    parser.add_argument("--history", action="store_true", help="Исторический режим: медленно, не более 5 активностей за запуск, увеличенный таймаут между запросами")
    args = parser.parse_args()

    logger.info("Starting Garmin->OSM sync (OAuth2)")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if not all([GARMIN_EMAIL, GARMIN_PASSWORD]):
        logger.error("Set GARMIN_EMAIL and GARMIN_PASSWORD in .env")
        return

    try:
        access_token = ensure_access_token()
    except Exception as e:
        logger.exception("Failed to obtain OSM access token: %s", e)
        return

    processed_ids = get_processed_ids()
    logger.info("Loaded %d processed ids", len(processed_ids))

    try:
        garmin_api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        garmin_api.login()
        logger.info("Garmin login successful")

        activities = garmin_api.get_activities(0, MAX_ACTIVITIES)
        new_activities = [act for act in activities if str(act.get("activityId")) not in processed_ids]

        if not new_activities:
            logger.info("No new activities to process")
            return

        # --- HISTORY MODE ---
        if args.history:
            max_per_run = 5
            sleep_time = 10
            logger.info("--history mode: processing up to %d activities, sleep %ds between uploads", max_per_run, sleep_time)
            new_activities = new_activities[-max_per_run:]
        else:
            sleep_time = 1

        for activity in reversed(new_activities):
            activity_id = activity.get("activityId")
            if not activity_id:
                continue

            name = activity.get("activityName") or f"Garmin Activity {activity_id}"
            type_ = activity.get("activityType", {}).get("typeKey", "unknown")
            start_time = activity.get("startTimeLocal") or activity.get("startTimeGMT") or "Unknown"
            gpx_path = DOWNLOAD_DIR / f"{activity_id}.gpx"

            try:
                logger.info("Processing activity %s (id=%s)", name, activity_id)
                gpx_data = garmin_api.download_activity(activity_id, dl_fmt=garmin_api.ActivityDownloadFormat.GPX)
                if isinstance(gpx_data, str):
                    gpx_bytes = gpx_data.encode("utf-8")
                else:
                    gpx_bytes = gpx_data

                with gpx_path.open("wb") as f:
                    f.write(gpx_bytes)

                desc = f"Garmin Activity: {name} on {start_time}"
                tags = f"garmin,sync,{type_}"
                visibility = "identifiable"

                if DRY_RUN:
                    logger.info("Dry run: would upload %s", gpx_path)
                    add_processed_id(activity_id)
                else:
                    resp = upload_gpx_with_bearer(access_token, gpx_path, desc, tags, visibility)
                    if resp.status_code == 401:
                        logger.warning("Upload returned 401. Trying to refresh token and retry once.")
                        # attempt refresh
                        tokens = load_tokens()
                        if tokens and tokens.get("refresh_token"):
                            tokens = refresh_tokens(tokens.get("refresh_token"))
                            access_token = tokens.get("access_token")
                            resp = upload_gpx_with_bearer(access_token, gpx_path, desc, tags, visibility)

                    if not resp.ok:
                        logger.error("Failed to upload %s. status=%s body=%s", gpx_path, resp.status_code, resp.text[:500])
                    else:
                        gpx_id = resp.text.strip()
                        logger.info("Uploaded activity %s to OSM (gpx id=%s)", activity_id, gpx_id)
                        add_processed_id(activity_id)

                time.sleep(sleep_time)

            except Exception as e:
                logger.exception("Error handling activity %s: %s", activity_id, e)
            finally:
                try:
                    if gpx_path.exists():
                        gpx_path.unlink()
                except Exception:
                    logger.exception("Failed to delete temporary file %s", gpx_path)

    except (GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError) as e:
        logger.exception("Garmin Connect error: %s", e)
    except Exception as e:
        logger.exception("Critical error: %s", e)
    finally:
        logger.info("Sync finished")


if __name__ == "__main__":
    main()
