# ============================================================================
#  Reolink Battery Dashboard – Docker image
# ============================================================================

# Base image: lightweight Python 3.12 (slim variant = no unnecessary packages).
FROM python:3.12-slim

# Working directory inside the container – the application code lands here.
WORKDIR /app

# Copy ONLY the dependency list first and install it.
# This lets Docker cache the pip layer separately: when only the source code
# changes (not the dependencies), the install step is skipped on rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the project (app.py, reolink.py, templates/, static/, …).
COPY . .

# Persistent data directory: token cache + battery history SQLite database.
# VOLUME ensures data survives container restarts and rebuilds.
RUN mkdir -p /appdata
VOLUME ["/appdata"]

# ---------------------------------------------------------------------------
#  CONFIGURATION (environment variables)
#  The values below are DEFAULTS – override them in docker-compose.yml / .env
#  without rebuilding the image.
# ---------------------------------------------------------------------------

# IP of the Reolink hub/NVR the dashboard connects to.
ENV NVR_IP=192.168.236.124
# Hub username.
ENV NVR_USER=admin
# Hub password (change this! prefer .env over hardcoding here).
ENV NVR_PASS=changeme

# Channel count is discovered automatically from the hub.
# NVR_CHANNEL is optional:  "auto"/empty = all hub channels;
# integer (e.g. 3) = limit to the first N channels (0..N-1).
ENV NVR_CHANNEL=auto

# How often (seconds) to poll the hub for battery state (300 = every 5 min).
# Lower = denser chart, but wakes sleeping battery cameras more frequently.
ENV POLL_INTERVAL=300

# Read timeout (s) for battery polling. The hub wakes battery cameras and can
# be slow to respond – increase if you see "Read timed out" in the logs.
ENV POLL_READ_TIMEOUT=45

# Default chart window shown when a camera detail is opened (hours).
# The UI also has 6h / 24h / 72h / 7d buttons to switch on the fly.
ENV HISTORY_HOURS=24

# How many days to keep history in the database. Older rows are auto-pruned.
ENV RETENTION_DAYS=30

# How often (seconds) to refresh the per-tile snapshot image.
# Snapshots wake the camera – a higher value preserves battery life.
ENV SNAPSHOT_TTL=300

# Port the server listens on inside the container.
ENV PORT=8080

# Data file paths (inside the /appdata volume – no need to change these).
ENV TOKEN_FILE=/appdata/reolink_token.json
ENV DB_FILE=/appdata/history.db

# Log level: DEBUG / INFO / WARNING / ERROR.
ENV LOG_LEVEL=INFO

# Number of Waitress worker threads. An open video stream holds one thread for
# a long time, so increase this if you stream many cameras simultaneously.
ENV WAITRESS_THREADS=16

# Expose the port (actual host mapping is done in docker-compose.yml).
EXPOSE 8080

# Start the Flask server + background poller thread.
CMD ["python", "app.py"]
