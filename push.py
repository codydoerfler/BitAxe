"""APNs push notifications for the BitAxe dashboard.

Sends native iOS pushes when a miner goes offline, recovers, faults, or runs
hot. Token-based auth with a team .p8 key (team-scoped — the same key PrintWatch
uses is valid for com.doerfler.bitaxe). Device tokens are registered by the app
via POST /api/register-push and stored in push_tokens.json.

Imported lazily and dependency-guarded so a missing package can never take the
dashboard down — if httpx/pyjwt aren't importable, pushes are simply skipped.
"""

import json
import logging
import os
import threading
import time

log = logging.getLogger("bitaxe.push")

# Team APNs auth key — team-scoped, so it signs pushes for any bundle id under
# team GKTGA5RWBU (shared with PrintWatch). Topic = the iOS app's bundle id.
TEAM_ID = "GKTGA5RWBU"
KEY_ID  = "9UK7RU4H8C"
P8_PATH = os.path.expanduser("~/printwatch/secrets/AuthKey_9UK7RU4H8C.p8")
TOPIC   = "com.doerfler.bitaxe"

HOT_THRESHOLD = 66.0   # °C — matches the app's red-zone alert threshold


def _semver(s):
    """Leading [major, minor, patch] from a version like 'v2.13.1-dirty'."""
    if not s:
        return None
    import re
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(s))
    if not m:
        return None
    return [int(m.group(i) or 0) for i in (1, 2, 3)]


def _fw_behind(current, latest):
    a, b = _semver(current), _semver(latest)
    if not a or not b:
        return False
    return a < b

DIR         = os.path.dirname(os.path.abspath(__file__))
TOKENS_PATH = os.path.join(DIR, "push_tokens.json")

_file_lock = threading.Lock()


# ── Token store ─────────────────────────────────────────────────────────────

def _load_tokens():
    try:
        with open(TOKENS_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_tokens(tokens):
    tmp = TOKENS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tokens, f)
    os.replace(tmp, TOKENS_PATH)


def register_token(token, name=None):
    """Add a device token (idempotent). Called from /api/register-push."""
    if not token:
        return
    with _file_lock:
        tokens = _load_tokens()
        if any(t.get("token") == token for t in tokens):
            return
        tokens.append({"token": token, "name": name, "ts": int(time.time())})
        _save_tokens(tokens)
        log.info("push: registered device %s… (%d total)", token[:8], len(tokens))


def _remove_token(token):
    with _file_lock:
        tokens = [t for t in _load_tokens() if t.get("token") != token]
        _save_tokens(tokens)
    log.info("push: pruned dead token %s…", token[:8])


# ── APNs sender ─────────────────────────────────────────────────────────────

class _APNs:
    """Token-based APNs sender. Tries production first, then sandbox, so it
    works for both TestFlight (production tokens) and Xcode (sandbox) installs;
    a token rejected by both environments is pruned."""

    PROD    = "https://api.push.apple.com"
    SANDBOX = "https://api.sandbox.push.apple.com"

    def __init__(self):
        self._jwt_cache = (0.0, "")
        self._lock = threading.Lock()

    def available(self):
        if not os.path.exists(P8_PATH):
            return False
        try:
            import jwt        # noqa: F401
            import httpx      # noqa: F401
        except Exception:
            return False
        return True

    def _jwt(self):
        import jwt as pyjwt
        with self._lock:
            issued, token = self._jwt_cache
            if token and time.time() - issued < 45 * 60:
                return token
            with open(P8_PATH) as f:
                key = f.read()
            token = pyjwt.encode(
                {"iss": TEAM_ID, "iat": int(time.time())},
                key, algorithm="ES256", headers={"kid": KEY_ID},
            )
            self._jwt_cache = (time.time(), token)
            return token

    def send(self, title, body, collapse_id=None):
        """Deliver to every registered device. Returns the number delivered."""
        import httpx
        tokens = _load_tokens()
        if not tokens:
            log.info("push: no devices registered; skipping '%s'", title)
            return 0
        payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
        sent = 0
        try:
            with httpx.Client(http2=True, timeout=20) as client:
                for dev in tokens:
                    tok = dev["token"]
                    r1 = self._deliver(client, self.PROD, tok, payload, collapse_id)
                    if r1 == "ok":
                        sent += 1
                        continue
                    if r1 == "bad":
                        r2 = self._deliver(client, self.SANDBOX, tok, payload, collapse_id)
                        if r2 == "ok":
                            sent += 1
                        elif r2 == "bad":
                            _remove_token(tok)
        except Exception as e:
            log.warning("push: client error %s", e)
        log.info("push: '%s' delivered to %d/%d devices", title, sent, len(tokens))
        return sent

    def _deliver(self, client, host, tok, payload, collapse_id):
        headers = {
            "authorization": f"bearer {self._jwt()}",
            "apns-topic": TOPIC,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        try:
            r = client.post(f"{host}/3/device/{tok}", json=payload, headers=headers)
        except Exception as e:
            log.warning("push: POST error %s", e)
            return "err"
        if r.status_code == 200:
            return "ok"
        if r.status_code in (400, 410) and ("BadDeviceToken" in r.text or "Unregistered" in r.text):
            return "bad"
        log.warning("push: %s -> %s %s", host, r.status_code, r.text[:140])
        return "err"


# ── State monitor ───────────────────────────────────────────────────────────

class PushMonitor:
    """Diffs each poll's miner readings against the last and pushes on the
    transitions that matter: offline, back-online, power fault, and overheat.
    Each transition fires once (collapse ids keep duplicates off the lock
    screen)."""

    def __init__(self):
        self.apns = _APNs()
        self.state = {}   # name -> {"online", "hot", "fault"}
        self.fw_notified = {}   # name -> latest version we've already announced
        self.enabled = self.apns.available()
        if self.enabled:
            log.info("push: APNs monitor enabled (topic %s)", TOPIC)
        else:
            log.warning("push: APNs unavailable (missing key or deps) — pushes disabled")

    def check(self, miners, latest_fw=None):
        """miners: [{"name": str, "online": bool, "info": dict|None}].
        latest_fw: the newest firmware version string, to alert when behind."""
        if not self.enabled:
            return
        for m in miners:
            name   = m["name"]
            online = bool(m["online"])
            info   = m.get("info") or {}
            prev   = self.state.get(name)

            # Firmware-update available — push once per (miner, new version).
            if online and latest_fw and _fw_behind(info.get("version"), latest_fw) \
                    and self.fw_notified.get(name) != latest_fw:
                self.fw_notified[name] = latest_fw
                self.apns.send(
                    f"⬆️ Firmware {latest_fw} for {name}",
                    f"AxeOS {info.get('version') or '?'} → {latest_fw} is available. "
                    "Open the app to see what's new and update.",
                    collapse_id=f"fw-{name}")

            if prev is not None:
                if prev["online"] and not online:
                    self.apns.send(
                        f"⛏️ {name} went offline",
                        "Stopped responding — it may have shut down, lost power, or dropped off Wi-Fi.",
                        collapse_id=f"offline-{name}")
                elif not prev["online"] and online:
                    self.apns.send(f"✅ {name} is back online", "Mining resumed.",
                                   collapse_id=f"offline-{name}")

            hot = False
            fault = False
            if online:
                temp     = info.get("temp")
                overheat = info.get("overheat_mode")
                pf       = (info.get("power_fault") or "").strip()
                hot   = bool(overheat) or (isinstance(temp, (int, float)) and temp >= HOT_THRESHOLD)
                fault = bool(pf)
                was_hot   = prev["hot"]   if prev else False
                was_fault = prev["fault"] if prev else False
                if hot and not was_hot:
                    if overheat:
                        self.apns.send(
                            f"\U0001f525 {name} overheated",
                            "AxeOS halted the ASIC to protect it. Check airflow and the fan.",
                            collapse_id=f"hot-{name}")
                    else:
                        self.apns.send(
                            f"\U0001f321️ {name} is running hot",
                            f"ASIC at {temp:.0f}°C (alert at {int(HOT_THRESHOLD)}°C). Check airflow.",
                            collapse_id=f"hot-{name}")
                if fault and not was_fault:
                    self.apns.send(
                        f"⚠️ {name} power fault",
                        f"{name} stopped to protect itself ({pf}). Check the power supply and cable.",
                        collapse_id=f"fault-{name}")

            self.state[name] = {"online": online, "hot": hot, "fault": fault}
