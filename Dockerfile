# Use a lightweight official Python image
FROM python:3.13.5-slim

# Set non-interactive frontend to avoid prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Install cron for scheduling tasks
RUN apt-get update && apt-get install -y cron

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the main application script
COPY main.py .

# Copy the crontab file to the cron directory
COPY crontab /etc/cron.d/sync-cron

# Give execution rights on the crontab file
RUN chmod 0644 /etc/cron.d/sync-cron

# Apply the crontab file
RUN crontab /etc/cron.d/sync-cron

# Create a log file and give it permissions so cron can write to it
RUN touch /var/log/cron.log
RUN chmod 0666 /var/log/cron.log

# Run cron in the foreground and tail the log file to keep the container running
# and to easily see the output with `docker logs`
CMD cron && tail -f /var/log/cron.log