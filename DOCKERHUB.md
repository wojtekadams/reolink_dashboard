# Reolink Battery Dashboard

Self-hosted web dashboard for Reolink battery cameras connected to a hub or NVR.

Polls every channel's battery state on a configurable interval, stores history in SQLite, and serves a live tile matrix with per-camera detail panels including a live FLV stream and a historical battery chart.

## Quick start

```bash
docker run -d \
  --name reolink-dashboard \
  --restart unless-stopped \
  -p 8112:8080 \
  -v $(pwd)/reolink_data:/appdata \
  -e NVR_IP=192.168.1.100 \
  -e NVR_USER=admin \
  -e NVR_PASS=your_password \
  reolink-dashboard:latest
```

Or with Docker Compose – clone the repo and run:

```bash
cp .env.example .env   # fill in hub IP, username, password
docker compose up -d --build
```

Dashboard → **http://localhost:8112**

## Environment variables

| Variable            | Default | Description                                                          |
|---------------------|---------|----------------------------------------------------------------------|
| `NVR_IP`            | –       | **Required.** Reolink hub / NVR IP address                           |
| `NVR_USER`          | admin   | Hub username                                                         |
| `NVR_PASS`          | –       | **Required.** Hub password                                           |
| `NVR_CHANNEL`       | auto    | `auto` = all cameras; integer = limit to first N channels            |
| `POLL_INTERVAL`     | 300     | Seconds between battery polls                                        |
| `POLL_READ_TIMEOUT` | 45      | HTTP read timeout for battery polls (increase if you see timeouts)   |
| `HISTORY_HOURS`     | 24      | Default chart window                                                 |
| `RETENTION_DAYS`    | 30      | Days of battery history kept in SQLite                               |
| `SNAPSHOT_TTL`      | 300     | Seconds the server caches snapshots (limits camera wake-ups)         |
| `PORT`              | 8080    | Internal port (map to any host port)                                 |
| `WAITRESS_THREADS`  | 16      | Worker threads (raise if streaming many cameras simultaneously)      |
| `LOG_LEVEL`         | INFO    | DEBUG / INFO / WARNING / ERROR                                       |

## Volumes

| Path        | Contents                                           |
|-------------|----------------------------------------------------|
| `/appdata`  | SQLite history database + Reolink session token    |

## Source

[github.com/your-username/reolink-dashboard](https://github.com/your-username/reolink-dashboard)
