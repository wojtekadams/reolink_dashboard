# Reolink Battery Dashboard

A self-hosted web dashboard for Reolink battery cameras connected to a hub or NVR.  
The backend logs into the hub API, caches a session token, and polls every channel's battery state on a configurable interval. The UI renders a live tile matrix; clicking any tile opens a detail panel with stats, a live FLV stream, and a historical battery chart.

![Dashboard preview](preview.html)

## Features

**Tile matrix** (auto-arranges to the number of connected cameras):
- Camera name + status badge (online / sleep / offline)
- Battery percentage with a colour-coded progress bar
- ⚡ icon while charging
- Cached snapshot thumbnail (server-side TTL so sleeping cameras aren't woken on every refresh)

**Camera detail panel** (click any tile):
- Battery %, voltage (V), temperature (°C)
- Charge status, low-power flag
- Serial / UID, channel number
- Live video preview (HTTP-FLV sub-stream proxied through the backend – credentials never reach the browser)
- Battery history chart: 6 h / 24 h / 72 h / 7 d with blue bands marking charging periods

**Storage section** – donut chart per hub disk (capacity, used, free).

## Architecture

```
reolink.py   – API client (login + token cache, batch poll_all, snapshot, FLV stream)
app.py       – Flask: background poller → SQLite, JSON API, HTML serving
templates/   – index.html (tile matrix + detail modal)
static/      – style.css, app.js (Chart.js from CDN, mpegts.js for FLV)
```

Battery history is stored in SQLite (`/appdata/history.db`). A background thread polls the hub every `POLL_INTERVAL` seconds: one `GetChannelStatus` call discovers active channels, then one `GetBatteryInfo` per channel (sequential, to isolate sleeping/offline cameras). The UI loads the in-memory latest state instantly; charts pull from the database.

## Quick start

```bash
cp .env.example .env        # fill in your hub IP, username and password
docker compose up -d --build
```

Dashboard → http://localhost:8112  
Demo without a hub → open `https://wojtekadams.github.io/reolink_dashboard/` in your browser.

## Configuration

All settings are environment variables. Set them in `.env` or directly in `docker-compose.yml`.

| Variable            | Default          | Description                                                                 |
|---------------------|------------------|-----------------------------------------------------------------------------|
| `NVR_IP`            | 192.168.1.100    | Hub / NVR IP address                                                        |
| `NVR_USER`          | admin            | Hub username                                                                |
| `NVR_PASS`          | *(empty)*        | Hub password                                                                |
| `NVR_CHANNEL`       | auto             | `auto` = discover all cameras; integer = limit to first N channels (0-based)|
| `POLL_INTERVAL`     | 300              | Seconds between battery polls (wakes sleeping cameras)                      |
| `POLL_READ_TIMEOUT` | 45               | HTTP read timeout for battery polls – increase if you see "Read timed out"  |
| `HISTORY_HOURS`     | 24               | Default chart window on panel open                                          |
| `RETENTION_DAYS`    | 30               | Days of battery history kept in the database                                |
| `SNAPSHOT_TTL`      | 300              | Seconds the server caches a snapshot before re-fetching                     |
| `PORT`              | 8080             | Internal container port                                                     |
| `WAITRESS_THREADS`  | 16               | Waitress worker threads (each open FLV stream holds one thread)             |
| `LOG_LEVEL`         | INFO             | DEBUG / INFO / WARNING / ERROR                                              |

## Running directly (no Docker)

```bash
pip install -r requirements.txt
NVR_IP=192.168.1.100 NVR_USER=admin NVR_PASS=secret python app.py
```

## REST API

| Endpoint                              | Returns                                              |
|---------------------------------------|------------------------------------------------------|
| `GET /api/cameras`                    | All channels + current battery state                 |
| `GET /api/camera/<ch>`                | Single channel detail (+ serial from GetDevInfo)     |
| `GET /api/camera/<ch>/history?hours=` | Battery history data points                          |
| `GET /api/camera/<ch>/snapshot`       | Current JPEG (served from server-side cache)         |
| `GET /api/camera/<ch>/stream`         | HTTP-FLV live stream proxy (`?q=main` for main stream)|
| `GET /api/hdd`                        | Hub disk info (GetHddInfo)                           |
| `GET /api/debug`                      | Raw hub responses for troubleshooting                |

## Notes

- The hub connection uses HTTPS with certificate verification disabled (`verify=False`) – Reolink hubs use self-signed certificates.
- Charging detection normalises multiple firmware variants: `chargeStatus` (`charging` / `chargeComplete` / `discharging` / `none`), `adapterStatus`, and current sign as a fallback.
- If `GetDevInfo` doesn't return a serial number for a channel, the `uid` from `GetChannelStatus` is shown instead.
- If your firmware reports HDD free/used in the opposite order, swap `capacity` and `size` in the `/api/hdd` handler in `app.py`.

## Battery impact

- **Snapshots** wake sleeping cameras. The server-side cache (`SNAPSHOT_TTL`, default 300 s) limits this to once per interval per camera.
- **Live stream** (`/stream`) wakes the camera and keeps it awake while the connection is open. The stream stops automatically when the detail panel is closed.
- Increase `SNAPSHOT_TTL` and avoid leaving detail panels open if battery longevity matters.

## LAN connectivity (Docker)

If the host can reach the hub but the container cannot, enable host networking:

```yaml
# docker-compose.yml
network_mode: host   # uncomment this
# ports:             # comment out the ports section
#   - 8112:8080
```
