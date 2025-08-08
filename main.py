import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import requests
from requests_oauthlib import OAuth1Session
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)

# --- CONFIGURATION ---
load_dotenv()
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
CONSUMER_KEY = os.getenv("OSM_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("OSM_CONSUMER_SECRET")
ACCESS_TOKEN = os.getenv("OSM_ACCESS_TOKEN")
ACCESS_SECRET = os.getenv("OSM_ACCESS_SECRET")

PROCESSED_ACTIVITIES_FILE = "processed_ids.txt"
DOWNLOAD_DIR = "downloads"

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_processed_ids():
    """
    Reads the set of processed activity IDs from a local file.
    Returns an empty set if the file doesn't exist.
    """
    try:
        if not os.path.exists(PROCESSED_ACTIVITIES_FILE):
            return set()
        with open(PROCESSED_ACTIVITIES_FILE, "r") as f:
            # Read IDs and filter out any empty lines
            return {line.strip() for line in f if line.strip()}
    except Exception as e:
        logging.error(f"Error reading processed IDs file: {e}")
        return set()

def add_processed_id(activity_id):
    with open(PROCESSED_ACTIVITIES_FILE, "a") as f:
        f.write(f"{activity_id}\n")

def upload_gpx_to_osm_oauth(gpx_filepath, description, tags, visibility):
    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=ACCESS_TOKEN,
        resource_owner_secret=ACCESS_SECRET
    )

    url = "https://www.openstreetmap.org/api/0.6/gpx/create"
    with open(gpx_filepath, "rb") as gpx_file:
        files = {"file": (os.path.basename(gpx_filepath), gpx_file, "application/gpx+xml")}
        data = {
            "description": description,
            "tags": tags,
            "visibility": visibility
        }
        response = oauth.post(url, files=files, data=data)
        if response.status_code != 200:
            raise Exception(f"GPX upload failed: {response.status_code} {response.text}")
        return response

def main():
    logging.info("--- Starting Garmin to OSM Sync Service ---")

    # Ensure download directory exists
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    if not all([GARMIN_EMAIL, GARMIN_PASSWORD, CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
        logging.error("FATAL: Missing credentials in .env file. Exiting.")
        return

    processed_ids = get_processed_ids()
    logging.info(f"Loaded {len(processed_ids)} already processed activity IDs.")

    try:
        logging.info("Logging in to Garmin Connect...")
        garmin_api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        garmin_api.login()
        logging.info("Garmin login successful.")

        activities = garmin_api.get_activities(0, 10)
        new_activities = [act for act in activities if str(act.get("activityId")) not in processed_ids]

        if not new_activities:
            logging.info("No new activities to sync.")
            return

        for activity in reversed(new_activities):
            activity_id = activity.get("activityId")
            if not activity_id:
                continue

            name = activity.get("activityName", f"Garmin Activity {activity_id}")
            type_ = activity.get("activityType", {}).get("typeKey", "unknown")
            start_time = activity.get("startTimeLocal", "Unknown time")
            gpx_filepath = os.path.join(DOWNLOAD_DIR, f"{activity_id}.gpx")

            try:
                logging.info(f"Processing activity: {name} (ID: {activity_id})")
                gpx_data = garmin_api.download_activity(
                    activity_id, dl_fmt=garmin_api.ActivityDownloadFormat.GPX
                )
                with open(gpx_filepath, "wb") as f:
                    f.write(gpx_data)

                description = f"Garmin Activity: {name} on {start_time}"
                tags = f"garmin,sync,{type_}"
                visibility = "identifiable"

                upload_gpx_to_osm_oauth(gpx_filepath, description, tags, visibility)
                logging.info(f"Uploaded activity {activity_id} to OSM.")
                add_processed_id(activity_id)

            except Exception as e:
                logging.error(f"Error with activity {activity_id}: {e}")
            finally:
                if os.path.exists(gpx_filepath):
                    os.remove(gpx_filepath)

    except (GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError) as e:
        logging.error(f"Garmin Connect error: {e}")
    except Exception as e:
        logging.error(f"Critical error: {e}")
    finally:
        logging.info("--- Sync Service Finished ---")

if __name__ == "__main__":
    main()
