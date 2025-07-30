import os
from dotenv import load_dotenv

load_dotenv()
import logging
from datetime import datetime
from garminconnect import (
    Garmin,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    GarminConnectAuthenticationError,
)
import requests
from osmapi import OsmApi, OsmApiError


# --- CONFIGURATION ---
# Credentials will be loaded from environment variables for security
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
OSM_USERNAME = os.getenv("OSM_USERNAME")
OSM_PASSWORD = os.getenv("OSM_PASSWORD")

# File to store IDs of already processed activities
# This file will be created in the same directory as the script
PROCESSED_ACTIVITIES_FILE = "processed_ids.txt"
# Directory to temporarily store downloaded GPX files
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
    """
    Appends a new processed activity ID to the local file.
    """
    try:
        with open(PROCESSED_ACTIVITIES_FILE, "a") as f:
            f.write(f"{activity_id}\n")
    except Exception as e:
        logging.error(f"Error writing to processed IDs file: {e}")

def upload_gpx_to_osm(gpx_filepath, description, tags, visibility, osm_username, osm_password):
    """
    Uploads a GPX file to OpenStreetMap using the GPX upload API.
    """
    url = "https://www.openstreetmap.org/api/0.6/gpx/create"
    with open(gpx_filepath, "rb") as gpx_file:
        files = {"file": (os.path.basename(gpx_filepath), gpx_file, "application/gpx+xml")}
        data = {
            "description": description,
            "tags": tags,
            "visibility": visibility
        }
        response = requests.post(
            url,
            files=files,
            data=data,
            auth=(osm_username, osm_password)
        )
        if response.status_code != 200:
            raise Exception(f"GPX upload failed: {response.status_code} {response.text}")
        return response

def main():
    """
    Main function to run the synchronization process.
    """
    logging.info("--- Starting Garmin to OSM Sync Service ---")

    # Ensure download directory exists
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    # Check for credentials
    if not all([GARMIN_EMAIL, GARMIN_PASSWORD, OSM_USERNAME, OSM_PASSWORD]):
        logging.error("FATAL: Environment variables for credentials are not set. Exiting.")
        return

    processed_ids = get_processed_ids()
    logging.info(f"Loaded {len(processed_ids)} already processed activity IDs.")

    try:
        # --- 1. CONNECT TO GARMIN AND GET ACTIVITIES ---
        logging.info("Attempting to log in to Garmin Connect...")
        garmin_api = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        garmin_api.login()
        logging.info("Garmin Connect login successful.")

        # Fetch the last 10 activities to check for new ones
        activities = garmin_api.get_activities(0, 10)
        if not activities:
            logging.info("No activities found in Garmin Connect account.")
            return

        # Filter out activities that have already been processed
        new_activities = [
            act for act in activities if str(act.get("activityId")) not in processed_ids
        ]

        if not new_activities:
            logging.info("No new activities to sync.")
            return

        logging.info(f"Found {len(new_activities)} new activities to sync.")

        # --- 2. CONNECT TO OSM AND PROCESS NEW ACTIVITIES ---
        logging.info("Connecting to OpenStreetMap API...")
        osm_api = OsmApi(username=OSM_USERNAME, password=OSM_PASSWORD)

        # Process from oldest to newest to maintain chronological order
        for activity in reversed(new_activities):
            activity_id = activity.get("activityId")
            if not activity_id:
                logging.warning(f"Found an activity with no ID. Skipping: {activity}")
                continue

            activity_name = activity.get("activityName", f"Garmin Activity {activity_id}")
            activity_type = activity.get("activityType", {}).get("typeKey", "unknown")
            start_time_local = activity.get("startTimeLocal", "Unknown time")

            gpx_filepath = os.path.join(DOWNLOAD_DIR, f"{activity_id}.gpx")

            try:
                logging.info(f"Processing activity: '{activity_name}' (ID: {activity_id})")

                # Download GPX file from Garmin
                gpx_data = garmin_api.download_activity(
                    activity_id, dl_fmt=garmin_api.ActivityDownloadFormat.GPX
                )
                with open(gpx_filepath, "wb") as f:
                    f.write(gpx_data)
                logging.info(f"GPX file downloaded to {gpx_filepath}")

                # Upload GPX file to OSM
                description = f"Garmin Activity: {activity_name} on {start_time_local}"
                tags = f"garmin,sync,{activity_type}"
                visibility = "identifiable"

                logging.info("Uploading to OpenStreetMap...")
                upload_gpx_to_osm(
                    gpx_filepath=gpx_filepath,
                    description=description,
                    tags=tags,
                    visibility=visibility,
                    osm_username=OSM_USERNAME,
                    osm_password=OSM_PASSWORD
                )
                logging.info(f"Successfully uploaded track for activity {activity_id} to OSM.")

                # If successful, add to processed list
                add_processed_id(activity_id)

            except OsmApiError as e:
                logging.error(f"OSM API Error while processing activity {activity_id}: {e}")
            except Exception as e:
                logging.error(f"An unexpected error occurred for activity {activity_id}: {e}")
            finally:
                # Clean up the downloaded GPX file
                if os.path.exists(gpx_filepath):
                    os.remove(gpx_filepath)
                    logging.info(f"Cleaned up temporary file {gpx_filepath}")

    except (GarminConnectConnectionError, GarminConnectTooManyRequestsError, GarminConnectAuthenticationError) as e:
        logging.error(f"Garmin Connect API Error: {e}")
    except Exception as e:
        logging.error(f"A critical error occurred: {e}")
    finally:
        logging.info("--- Sync Service Finished ---")

if __name__ == "__main__":
    main()
