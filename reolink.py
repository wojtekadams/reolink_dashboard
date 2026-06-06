"""
Reolink Hub / NVR API client.

Handles login + token caching and exposes the few commands the dashboard
needs: channel status, per-channel battery info, device info, HDD info and
snapshots. All requests go to the single /cgi-bin/api.cgi endpoint and can be
batched (Reolink accepts a JSON list of commands in one POST).
"""

import json
import logging
import os
import threading
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("reolink")

# ---------------- CONFIG ----------------
NVR_IP = os.environ.get("NVR_IP", "192.168.236.124")
USERNAME = os.environ.get("NVR_USER", "admin")
PASSWORD = os.environ.get("NVR_PASS", "")

# Channel count is discovered dynamically from GetChannelStatus.
# NVR_CHANNEL is optional:
#   - empty / "auto" / "all" / "0"  -> show all hub channels (dynamic)
#   - integer (e.g. 3)              -> limit to the first N channels (0..N-1)
def _parse_channel_limit(raw):
    raw = (raw or "").strip().lower()
    if raw in ("", "auto", "all", "0"):
        return None
    try:
        return max(int(raw), 0) or None
    except ValueError:
        return None

CHANNEL_LIMIT = _parse_channel_limit(os.environ.get("NVR_CHANNEL", "auto"))

TOKEN_FILE = os.environ.get("TOKEN_FILE", "/appdata/reolink_token.json")
# Reolink leases tokens for 3600s; we report the value returned by the hub but
# keep a conservative fallback here.
TOKEN_VALID_SECONDS = int(os.environ.get("TOKEN_VALID_SECONDS", 3600))

# Interactive requests (snapshot, status) – short timeout.
HTTP_TIMEOUT = (5, int(os.environ.get("HTTP_READ_TIMEOUT", 15)))
# Battery polling can be slow – the hub wakes sleeping battery cameras. Longer timeout.
POLL_TIMEOUT = (5, int(os.environ.get("POLL_READ_TIMEOUT", 45)))


class ReolinkClient:
    """Thread-safe-ish client. A single lock guards token refresh so concurrent
    requests don't all trigger a login storm."""

    def __init__(self):
        self._token = None
        self._token_time = 0
        self._lease = TOKEN_VALID_SECONDS
        self._lock = threading.Lock()
        self.base_url = f"https://{NVR_IP}/cgi-bin/api.cgi"

    # ---------------- TOKEN ----------------
    def _save_token(self):
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                json.dump(
                    {"token": self._token, "time": self._token_time, "lease": self._lease},
                    f,
                )
        except Exception as e:
            logger.warning(f"Could not persist token: {e}")

    def _load_token(self):
        if not os.path.exists(TOKEN_FILE):
            return False
        try:
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            lease = data.get("lease", TOKEN_VALID_SECONDS)
            if time.time() - data["time"] < lease - 60:
                self._token = data["token"]
                self._token_time = data["time"]
                self._lease = lease
                logger.info("Token loaded from file")
                return True
        except Exception:
            pass
        return False

    def _login(self):
        payload = [{
            "cmd": "Login",
            "param": {"User": {"userName": USERNAME, "password": PASSWORD}},
        }]
        try:
            r = requests.post(
                self.base_url, params={"cmd": "Login"},
                json=payload, verify=False, timeout=HTTP_TIMEOUT,
            )
            resp = r.json()
            if isinstance(resp, list) and "value" in resp[0]:
                tok = resp[0]["value"]["Token"]
                self._token = tok["name"]
                self._lease = int(tok.get("leaseTime", TOKEN_VALID_SECONDS))
                self._token_time = time.time()
                self._save_token()
                logger.info("Logged in, new token acquired")
                return True
            logger.error(f"Login failed, unexpected response: {resp}")
        except requests.exceptions.RequestException as e:
            # Network/connection problems (host down, no route, timeout) –
            # log a concise line instead of a scary stack trace.
            logger.warning(f"Cannot reach hub at {NVR_IP}: {e.__class__.__name__}")
        except Exception as e:
            logger.warning(f"Login error: {e}")
        return False

    def get_token(self, force=False):
        with self._lock:
            now = time.time()
            if not force and self._token and now - self._token_time < self._lease - 60:
                return self._token
            if not force and self._load_token():
                return self._token
            if self._login():
                return self._token
            return None

    # ---------------- CORE REQUEST ----------------
    def _post(self, payload, retry=True, timeout=None):
        """POST a batch payload. Refreshes the token once on auth failure."""
        token = self.get_token()
        if not token:
            return None
        try:
            r = requests.post(
                self.base_url, params={"token": token},
                json=payload, verify=False, timeout=timeout or HTTP_TIMEOUT,
            )
            resp = r.json()
        except Exception as e:
            logger.warning(f"Request error: {e}")
            return None

        # Detect expired/invalid token -> refresh once and retry.
        if retry and self._looks_like_auth_error(resp):
            logger.info("Token rejected, refreshing")
            self.get_token(force=True)
            return self._post(payload, retry=False, timeout=timeout)
        return resp

    @staticmethod
    def _looks_like_auth_error(resp):
        if not isinstance(resp, list):
            return False
        for item in resp:
            err = item.get("error") if isinstance(item, dict) else None
            if err and err.get("rspCode") in (-6, -7):  # auth / token errors
                return True
        return False

    # ---------------- HIGH LEVEL ----------------
    def get_channel_status(self, timeout=None):
        resp = self._post([{"cmd": "GetChannelStatus"}], timeout=timeout)
        if resp and "value" in resp[0]:
            return resp[0]["value"]
        return None

    def get_battery_info(self, channel):
        resp = self._post([
            {"cmd": "GetBatteryInfo", "action": 0, "param": {"channel": channel}}
        ])
        if resp and "value" in resp[0]:
            return resp[0]["value"].get("Battery")
        return None

    def get_dev_info(self, channel=None):
        param = {} if channel is None else {"channel": channel}
        resp = self._post([{"cmd": "GetDevInfo", "action": 0, "param": param}])
        if resp and "value" in resp[0]:
            return resp[0]["value"].get("DevInfo")
        return None

    def get_hdd_info(self):
        resp = self._post([{"cmd": "GetHddInfo"}])
        if resp and "value" in resp[0]:
            return resp[0]["value"].get("HddInfo")
        return None

    def discover_channels(self):
        """Return the list of channel numbers the hub currently reports,
        applying the optional NVR_CHANNEL cap. Always reflects what the hub
        actually has, so adding/removing a camera is picked up automatically."""
        status_val = self.get_channel_status(timeout=POLL_TIMEOUT) or {}
        statuses = status_val.get("status", [])
        chans = sorted(
            st["channel"] for st in statuses if st.get("channel") is not None
        )
        if CHANNEL_LIMIT is not None:
            chans = [c for c in chans if c < CHANNEL_LIMIT]
        return chans, statuses

    def poll_all(self):
        """Dynamic poll: discover channels from GetChannelStatus, then fetch
        battery info only for the channels that actually exist. Returns a dict
        keyed by channel with merged status + battery."""
        channels, statuses = self.discover_channels()
        if not channels:
            return {}

        result = {}
        status_by_ch = {st.get("channel"): st for st in statuses}
        for ch in channels:
            st = status_by_ch.get(ch, {})
            result[ch] = {
                "channel": ch,
                "name": st.get("name") or f"CH{ch}",
                "online": st.get("online", 0),
                "sleep": st.get("sleep", 0),
                "uid": st.get("uid"),
                "typeInfo": st.get("typeInfo"),
                "battery": None,
            }

        # Battery is fetched PER CHANNEL (not as one batch): some hubs reject
        # the whole batch if a single channel can't answer (asleep/offline),
        # which would blank every camera. Per-channel isolates failures.
        for ch in channels:
            try:
                single = self._post(
                    [{"cmd": "GetBatteryInfo", "action": 0, "param": {"channel": ch}}],
                    timeout=POLL_TIMEOUT,
                )
                if single and isinstance(single[0], dict):
                    result[ch]["battery"] = single[0].get("value", {}).get("Battery")
            except Exception as e:
                logger.warning(f"Battery fetch failed ch{ch}: {e}")

        return result

    def snapshot(self, channel):
        token = self.get_token()
        if not token:
            return None
        try:
            r = requests.get(
                self.base_url,
                params={
                    "cmd": "Snap", "channel": channel,
                    "token": token, "rs": str(int(time.time())),
                },
                verify=False, timeout=HTTP_TIMEOUT,
            )
            if r.headers.get("Content-Type", "").startswith("image"):
                return r.content
        except Exception as e:
            logger.warning(f"Snapshot error ch{channel}: {e}")
        return None

    def open_flv(self, channel, sub=True):
        """Open the hub's HTTP-FLV live stream for a channel and return the
        streaming requests.Response. Credentials stay server-side; the Flask
        layer proxies the bytes to the browser. `sub` uses the lighter
        sub-stream (recommended, far gentler on battery cameras)."""
        stream = f"channel{channel}_{'sub' if sub else 'main'}.bcs"
        url = f"https://{NVR_IP}/flv"
        params = {
            "port": 1935, "app": "bcs", "stream": stream,
            "user": USERNAME, "password": PASSWORD,
        }
        try:
            r = requests.get(
                url, params=params, verify=False, stream=True,
                timeout=(5, 30),
            )
            if r.status_code == 200:
                return r
            logger.warning(f"FLV stream ch{channel} status {r.status_code}")
            r.close()
        except Exception as e:
            logger.warning(f"FLV stream error ch{channel}: {e}")
        return None


client = ReolinkClient()
