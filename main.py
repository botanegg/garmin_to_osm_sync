"""
Garmin Connect -> OpenStreetMap GPX sync (OAuth2)

"""

import os
import json
import logging
import time
import webbrowser
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs
from datetime import datetime, timedelta

import requests
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
        expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token"),
        "expires_at": expires_at,
        "scope": j.get("scope"),
        "obtained_at": datetime.utcnow().isoformat(),
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
        expires_at = (datetime.utcnow() + timedelta(seconds=int(expires_in))).isoformat()

    tokens = {
        "access_token": j.get("access_token"),
        "refresh_token": j.get("refresh_token", refresh_token),
        "expires_at": expires_at,
        "scope": j.get("scope"),
        "obtained_at": datetime.utcnow().isoformat(),
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
        if datetime.utcnow() + timedelta(seconds=60) >= expires_dt:
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

def get_processed_ids():
    try:
        if not PROCESSED_ACTIVITIES_FILE.exists():
            return set()
        with PROCESSED_ACTIVITIES_FILE.open("r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except Exception:
        logger.exception("Failed to read processed ids file")
        return set()


def add_processed_id(activity_id):
    try:
        with PROCESSED_ACTIVITIES_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{activity_id}\n")
    except Exception:
        logger.exception("Failed to append processed id")

# --- MAIN ---

def main():
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

                time.sleep(1)

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
