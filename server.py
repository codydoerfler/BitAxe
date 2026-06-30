#!/usr/bin/env python3
"""BitAxe dashboard — static files, API proxy, miner config, history logging."""

import http.server
import urllib.request
import urllib.error
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlparse, parse_qs

try:
    import push                      # APNs push (offline / hot / fault alerts)
except Exception:                    # never let push problems break the dashboard
    push = None

try:
    import recover                   # auto-recovery: safe-reset + restart on overheat
except Exception:                    # never let recovery problems break the dashboard
    recover = None

try:
    import rates                     # configurable electricity rates (region/flat/TOU)
except Exception:
    rates = None

try:
    import benchmark                 # automated overclock sweep
    BENCH = benchmark.Benchmarker()
except Exception:
    benchmark = None
    BENCH = None

PORT    = 3000
DIR     = os.path.dirname(os.path.abspath(__file__))
CFG     = os.path.join(DIR, "config.json")
DB_PATH = os.path.join(DIR, "history.db")

POLL_INTERVAL = 60  # seconds between readings

# ESP-Miner firmware releases (for the dashboard's update UI + version check).
GH_LATEST   = "https://api.github.com/repos/bitaxeorg/ESP-Miner/releases/latest"
GH_UA       = "BitAxeDashboard/1.0"
OTA_TIMEOUT = 120   # seconds — a firmware image is ~1.5 MB over the LAN
_fw_cache   = {"ts": 0.0, "data": None}   # latest-release lookup, cached 10 min

# Time-of-use electricity rates ($/kWh). On-peak = non-holiday weekdays 5–9 PM.
# (Off-peak ≈ on-peak ÷ 2.7. Summer = Jun–Sep, winter = rest.)
RATE_SUMMER = {"on": 0.213, "off": 0.079}
RATE_WINTER = {"on": 0.184, "off": 0.068}


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if os.path.exists(CFG):
        with open(CFG) as f:
            return json.load(f)
    default = {"miners": [{"ip": "http://192.168.4.154", "name": "BitAxe 1"}]}
    save_config(default)
    return default


def save_config(cfg):
    with open(CFG, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              INTEGER NOT NULL,
            miner_ip        TEXT NOT NULL,
            hashrate        REAL,
            temp            REAL,
            power           REAL,
            shares_accepted INTEGER,
            shares_rejected INTEGER,
            efficiency      REAL,
            frequency       INTEGER,
            core_voltage    INTEGER,
            pool_difficulty INTEGER
        )
    """)
    # migrate existing DB if pool_difficulty column is missing
    try:
        conn.execute("ALTER TABLE readings ADD COLUMN pool_difficulty INTEGER")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_miner_ts ON readings(miner_ip, ts)")
    conn.commit()
    conn.close()


def record_reading(miner_ip, d):
    hr   = d.get("hashRate")
    pw   = d.get("power")
    eff  = round(hr / pw, 2) if hr and pw else None
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO readings
            (ts, miner_ip, hashrate, temp, power, shares_accepted, shares_rejected,
             efficiency, frequency, core_voltage, pool_difficulty)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()), miner_ip,
        hr, d.get("temp"), pw,
        d.get("sharesAccepted"), d.get("sharesRejected"),
        eff, d.get("frequency"), d.get("coreVoltage"),
        d.get("poolDifficulty")
    ))
    conn.commit()
    conn.close()


def query_history(miner_ip, hours):
    # bucket size: keep ~80–120 points regardless of range
    if hours <= 1:
        bucket = 60
    elif hours <= 6:
        bucket = 300
    elif hours <= 24:
        bucket = 900
    else:
        bucket = 3600

    since = int(time.time()) - hours * 3600
    conn  = sqlite3.connect(DB_PATH)
    cur   = conn.cursor()
    cur.execute("""
        SELECT
            (ts / :b) * :b                   AS t,
            AVG(hashrate)                     AS hashrate,
            AVG(temp)                         AS temp,
            AVG(power)                        AS power,
            AVG(efficiency)                   AS efficiency,
            MAX(shares_accepted)              AS shares_acc,
            MAX(shares_rejected)              AS shares_rej
        FROM readings
        WHERE miner_ip = :ip AND ts > :since
        GROUP BY t
        ORDER BY t
    """, {"b": bucket, "ip": miner_ip, "since": since})
    rows = cur.fetchall()
    conn.close()

    # compute share deltas (rate per interval) instead of cumulative
    result = {"timestamps": [], "hashrate": [], "temp": [], "power": [],
              "efficiency": [], "shares_accepted": [], "shares_rejected": []}
    prev_acc = prev_rej = None
    for row in rows:
        ts, hr, temp, pw, eff, acc, rej = row
        result["timestamps"].append(ts * 1000)  # ms for JS
        result["hashrate"].append(round(hr, 2)   if hr   is not None else None)
        result["temp"].append(round(temp, 1)      if temp is not None else None)
        result["power"].append(round(pw, 1)       if pw   is not None else None)
        result["efficiency"].append(round(eff, 2) if eff  is not None else None)
        result["shares_accepted"].append((acc - prev_acc) if prev_acc is not None and acc is not None else None)
        result["shares_rejected"].append((rej - prev_rej) if prev_rej is not None and rej is not None else None)
        prev_acc, prev_rej = acc, rej

    return result


# ── Host health ────────────────────────────────────────────────────────────────

def _macos_mem_pct():
    """Used physical memory as a percent (app memory + wired), best-effort via
    vm_stat. Returns None on non-macOS or any parse failure."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
        page = 4096
        m = re.search(r"page size of (\d+) bytes", out)
        if m:
            page = int(m.group(1))
        stats = {}
        for line in out.splitlines():
            mm = re.match(r'"?([\w ]+?)"?:\s+(\d+)\.', line)
            if mm:
                stats[mm.group(1).strip()] = int(mm.group(2))
        free = stats.get("Pages free", 0) + stats.get("Pages inactive", 0) + stats.get("Pages speculative", 0)
        total = sum(stats.get(k, 0) for k in (
            "Pages free", "Pages active", "Pages inactive", "Pages speculative", "Pages wired down"))
        if total <= 0:
            return None
        return round((total - free) / total * 100)
    except Exception:
        return None


# ── Firmware (ESP-Miner releases) ───────────────────────────────────────────────

def fetch_latest_firmware():
    """Latest ESP-Miner release: version + asset download URLs. Cached 10 min;
    serves the stale entry if GitHub is briefly unreachable."""
    now = time.time()
    if _fw_cache["data"] and now - _fw_cache["ts"] < 600:
        return _fw_cache["data"]
    try:
        req = urllib.request.Request(
            GH_LATEST, headers={"User-Agent": GH_UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rel = json.load(r)
    except Exception:
        return _fw_cache["data"]
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
    data = {
        "version":       rel.get("tag_name"),
        "name":          rel.get("name"),
        "published":     rel.get("published_at"),
        "url":           rel.get("html_url"),
        "esp_miner_url": assets.get("esp-miner.bin"),
        "www_url":       assets.get("www.bin"),
    }
    _fw_cache.update(ts=now, data=data)
    return data


def _download_asset(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": GH_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _flash(ip, path, blob):
    """POST a firmware image to one AxeOS OTA endpoint as raw octet-stream."""
    req = urllib.request.Request(
        ip + path, data=blob, method="POST",
        headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=OTA_TIMEOUT) as r:
        return 200 <= r.status < 300


# ── Background collector ───────────────────────────────────────────────────────

def collector_loop(monitor=None, recoverer=None):
    while True:
        cfg = load_config()
        snapshot = []
        for miner in cfg["miners"]:
            info = None
            try:
                url = miner["ip"] + "/api/system/info"
                with urllib.request.urlopen(url, timeout=8) as r:
                    info = json.load(r)
                record_reading(miner["ip"], info)
            except Exception:
                info = None
            snapshot.append({"name": miner["name"], "ip": miner["ip"],
                             "online": info is not None, "info": info})
        if monitor is not None:
            try:
                monitor.check(snapshot)
            except Exception as e:
                print(f"push monitor error: {e}")
        # Auto-recover overheating miners (opt-out via "auto_recover": false).
        if recoverer is not None and cfg.get("auto_recover", True):
            try:
                recoverer.check(snapshot)
            except Exception as e:
                print(f"auto-recover error: {e}")
        time.sleep(POLL_INTERVAL)


# ── Request handler ───────────────────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/system/info":
            cfg = load_config()
            self._proxy(cfg["miners"][0]["ip"] + "/api/system/info")
        elif p == "/api/btc-price":
            self._btc_price()
        elif p == "/api/pi-temp":
            self._pi_temp()
        elif p == "/api/host-load":
            self._host_load()
        elif p == "/api/miners":
            self._get_miners()
        elif p == "/api/history":
            self._get_history()
        elif p == "/api/tickets":
            self._get_tickets()
        elif p == "/api/energy":
            self._get_energy()
        elif p == "/api/firmware/latest":
            self._firmware_latest()
        elif p == "/api/rates":
            self._get_rates()
        elif re.match(r"^/api/miner/(\d+)/benchmark/status$", p):
            self._benchmark_status(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif re.match(r"^/api/miner/\d+/", self.path):
            self._miner_proxy("GET")
        else:
            super().do_GET()

    def do_PATCH(self):
        if re.match(r"^/api/miner/\d+/", self.path):
            self._miner_proxy("PATCH")
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/api/miners":
            self._add_miner()
        elif p == "/api/register-push":
            self._register_push()
        elif re.match(r"^/api/miner/(\d+)/update-firmware$", p):
            self._update_firmware(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif re.match(r"^/api/miner/(\d+)/safe-reset$", p):
            self._safe_reset(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif p == "/api/rates":
            self._set_rates()
        elif re.match(r"^/api/miner/(\d+)/benchmark/start$", p):
            self._benchmark_start(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif re.match(r"^/api/miner/(\d+)/benchmark/stop$", p):
            self._benchmark_stop(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif re.match(r"^/api/miner/(\d+)/benchmark/apply-best$", p):
            self._benchmark_apply_best(int(re.match(r"^/api/miner/(\d+)/", p).group(1)))
        elif re.match(r"^/api/miner/\d+/", self.path):
            self._miner_proxy("POST")
        else:
            self._respond(404, {"error": "not found"})

    def do_DELETE(self):
        m = re.match(r"^/api/miners/(\d+)$", self.path)
        if m:
            self._remove_miner(int(m.group(1)))
        else:
            self._respond(404, {"error": "not found"})

    # ── API handlers ──────────────────────────────────────────────────────────

    def _get_miners(self):
        cfg = load_config()
        results = []
        for i, miner in enumerate(cfg["miners"]):
            try:
                with urllib.request.urlopen(miner["ip"] + "/api/system/info", timeout=5) as r:
                    info = json.load(r)
                results.append({"index": i, "name": miner["name"],
                                "ip": miner["ip"], "online": True, "info": info})
            except Exception:
                results.append({"index": i, "name": miner["name"],
                                "ip": miner["ip"], "online": False, "info": None})
        self._respond(200, results)

    def _get_tickets(self):
        qs        = parse_qs(urlparse(self.path).query)
        miner_idx = int(qs.get("miner", ["0"])[0])
        cfg       = load_config()
        if miner_idx >= len(cfg["miners"]):
            return self._respond(404, {"error": "miner not found"})
        miner_ip = cfg["miners"][miner_idx]["ip"]
        conn     = sqlite3.connect(DB_PATH)
        cur      = conn.cursor()

        # daily ticket counts for past 7 days
        cur.execute("""
            SELECT
                date(ts, 'unixepoch', 'localtime')   AS day,
                AVG(hashrate)                         AS avg_hr,
                AVG(COALESCE(pool_difficulty, 8192))  AS avg_diff
            FROM readings
            WHERE miner_ip = ? AND ts > ?
            GROUP BY day
            ORDER BY day
        """, (miner_ip, int(time.time()) - 7 * 86400))
        rows = cur.fetchall()

        # today's running total (readings since midnight local)
        import datetime
        midnight = int(datetime.datetime.combine(
            datetime.date.today(), datetime.time.min).timestamp())
        cur.execute("""
            SELECT AVG(hashrate), AVG(COALESCE(pool_difficulty, 8192)), COUNT(*)
            FROM readings
            WHERE miner_ip = ? AND ts >= ?
        """, (miner_ip, midnight))
        today_row = cur.fetchone()
        conn.close()

        def tickets(hr, diff, seconds):
            if not hr or not diff:
                return 0
            return (hr * 1e9 * seconds) / (diff * 4294967296)

        days, counts = [], []
        for row in rows:
            day, avg_hr, avg_diff = row
            days.append(day)
            counts.append(round(tickets(avg_hr, avg_diff, 86400)))

        today_hr, today_diff, today_readings = today_row if today_row else (None, None, 0)
        elapsed = int(time.time()) - midnight
        today_count = round(tickets(today_hr, today_diff, elapsed))

        self._respond(200, {
            "days":          days,
            "counts":        counts,
            "today":         today_count,
            "today_rate":    round(tickets(today_hr, today_diff, 86400)) if today_hr else 0,
            "today_hr":      round(today_hr, 1) if today_hr else None,
            "today_diff":    int(today_diff) if today_diff else 8192,
        })

    def _get_energy(self):
        qs        = parse_qs(urlparse(self.path).query)
        miner_idx = int(qs.get("miner", ["0"])[0])
        cfg       = load_config()
        if miner_idx >= len(cfg["miners"]):
            return self._respond(404, {"error": "miner not found"})
        miner_ip = cfg["miners"][miner_idx]["ip"]
        import datetime

        # Configurable rates (region/flat/TOU); falls back to the original CO TOU.
        rates_cfg = cfg.get("rates") or (rates.default_rates() if rates else None)
        oph = rates.on_peak_hours(rates_cfg) if rates else [17, 20]

        # on-peak = weekday (Mon–Fri = strftime %w 1–5), local hour oph[0]–oph[1]
        ON = ("CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) BETWEEN 1 AND 5 "
              f"AND CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) BETWEEN {oph[0]} AND {oph[1]}")

        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        # split each day's power into on-peak / off-peak sums (all history)
        cur.execute(f"""
            SELECT date(ts, 'unixepoch', 'localtime') AS day,
                   SUM(CASE WHEN {ON} THEN power ELSE 0 END) AS p_on,
                   SUM(CASE WHEN {ON} THEN 0 ELSE power END) AS p_off
            FROM readings WHERE miner_ip = ?
            GROUP BY day ORDER BY day
        """, (miner_ip,))
        rows = cur.fetchall()

        midnight = int(datetime.datetime.combine(
            datetime.date.today(), datetime.time.min).timestamp())
        cur.execute(f"""
            SELECT SUM(CASE WHEN {ON} THEN power ELSE 0 END),
                   SUM(CASE WHEN {ON} THEN 0 ELSE power END),
                   AVG(power)
            FROM readings WHERE miner_ip = ? AND ts >= ?
        """, (miner_ip, midnight))
        t_on, t_off, t_avg = cur.fetchone()
        conn.close()

        def kwh(p):       # each reading covers POLL_INTERVAL seconds
            return (p * POLL_INTERVAL / 3_600_000) if p else 0.0
        def rates_for(day):
            if rates:
                return rates.resolve(rates_cfg, day)
            return RATE_SUMMER if 6 <= int(day.split("-")[1]) <= 9 else RATE_WINTER

        days, counts, costs = [], [], []
        for day, p_on, p_off in rows:
            r = rates_for(day)
            on_k, off_k = kwh(p_on), kwh(p_off)
            days.append(day)
            counts.append(round(on_k + off_k, 3))
            costs.append(round(on_k * r["on"] + off_k * r["off"], 2))

        # all-time cumulative totals, then keep the last 7 days for the chart
        total_kwh  = round(sum(counts), 3)
        total_cost = round(sum(costs), 2)
        since      = days[0] if days else None
        days, counts, costs = days[-7:], counts[-7:], costs[-7:]

        today  = datetime.date.today()
        r      = rates_for(today.isoformat())
        avg_w  = t_avg or 0
        is_wd  = today.weekday() < 5
        # projected full-day cost at today's avg draw (weekday = 4 h on-peak + 20 h off)
        proj_cost = (avg_w*4/1000)*r["on"] + (avg_w*20/1000)*r["off"] if is_wd \
                    else (avg_w*24/1000)*r["off"]

        now    = datetime.datetime.now()
        now_on = now.weekday() < 5 and 17 <= now.hour <= 20
        self._respond(200, {
            "days":           days,
            "counts":         counts,
            "costs":          costs,
            "today":          round(kwh(t_on) + kwh(t_off), 3),
            "today_cost":     round(kwh(t_on) * r["on"] + kwh(t_off) * r["off"], 2),
            "projected":      round(avg_w * 24 / 1000, 3),
            "projected_cost": round(proj_cost, 2),
            "now_onpeak":     now_on,
            "now_rate":       r["on"] if now_on else r["off"],
            "total_cost":     total_cost,
            "total_kwh":      total_kwh,
            "since":          since,
        })

    def _get_history(self):
        qs        = parse_qs(urlparse(self.path).query)
        miner_idx = int(qs.get("miner", ["0"])[0])
        hours     = int(qs.get("hours", ["24"])[0])
        cfg       = load_config()
        if miner_idx >= len(cfg["miners"]):
            return self._respond(404, {"error": "miner not found"})
        miner_ip = cfg["miners"][miner_idx]["ip"]
        self._respond(200, query_history(miner_ip, hours))

    def _miner_proxy(self, method):
        m = re.match(r"^/api/miner/(\d+)/(.+)$", self.path)
        if not m:
            return self._respond(404, {"error": "not found"})
        idx, path = int(m.group(1)), m.group(2)
        cfg = load_config()
        if idx >= len(cfg["miners"]):
            return self._respond(404, {"error": "miner not found"})
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else None
        self._proxy(cfg["miners"][idx]["ip"] + "/" + path, method=method, body=body)

    def _add_miner(self):
        length = int(self.headers.get("Content-Length", 0))
        data   = json.loads(self.rfile.read(length))
        cfg    = load_config()
        ip     = data.get("ip", "").rstrip("/")
        if not ip.startswith("http"):
            ip = "http://" + ip
        name = data.get("name") or f"BitAxe {len(cfg['miners']) + 1}"
        cfg["miners"].append({"ip": ip, "name": name})
        save_config(cfg)
        self._respond(200, cfg)

    def _remove_miner(self, idx):
        cfg = load_config()
        if idx <= 0 or idx >= len(cfg["miners"]):
            return self._respond(400, {"error": "cannot remove primary miner"})
        cfg["miners"].pop(idx)
        save_config(cfg)
        self._respond(200, cfg)

    def _btc_price(self):
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
        req = urllib.request.Request(url, headers={"User-Agent": "BitAxeDashboard/1.0"})
        self._proxy(url, req=req)

    def _pi_temp(self):
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                raw = int(f.read().strip())
            temp = round(raw / 1000.0, 1)
            self._respond(200, {"temp": temp})
        except Exception as e:
            self._respond(503, {"error": str(e)})

    def _host_load(self):
        """Dashboard host health — CPU load as a percent of cores (works on the
        Mac mini, where a real CPU temp needs root) plus memory pressure. The app
        reads pct/load1/cores; mem_pct enriches the card when present."""
        try:
            load1, load5, _ = os.getloadavg()
            cores = os.cpu_count() or 1
            data = {
                "pct":   round(load1 / cores * 100),
                "load1": round(load1, 2),
                "load5": round(load5, 2),
                "cores": cores,
            }
            mem = _macos_mem_pct()
            if mem is not None:
                data["mem_pct"] = mem
            self._respond(200, data)
        except Exception as e:
            self._respond(503, {"error": str(e)})

    def _register_push(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            return self._respond(400, {"error": "bad json"})
        token = (data.get("token") or "").strip()
        if not token:
            return self._respond(400, {"error": "missing token"})
        if push is not None:
            try:
                push.register_token(token, data.get("name"))
            except Exception as e:
                return self._respond(500, {"error": str(e)})
        self._respond(200, {"ok": True})

    # ── Firmware + recovery ────────────────────────────────────────────────────

    def _firmware_latest(self):
        info = fetch_latest_firmware()
        if not info:
            return self._respond(502, {"error": "could not reach GitHub releases"})
        self._respond(200, info)

    def _miner_ip_info(self, idx):
        """(ip, info|{}) for a configured miner, or (None, None) if out of range."""
        cfg = load_config()
        if idx >= len(cfg["miners"]):
            return None, None
        ip = cfg["miners"][idx]["ip"]
        try:
            with urllib.request.urlopen(ip + "/api/system/info", timeout=6) as r:
                return ip, json.load(r)
        except Exception:
            return ip, {}

    def _safe_reset(self, idx):
        """Reset one miner to conservative tuning + auto fan, then restart."""
        ip, info = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        if recover is None:
            return self._respond(503, {"error": "recovery module unavailable"})
        params = recover.apply_safe_reset(ip, info)
        if params:
            self._respond(200, {"ok": True, "applied": params})
        else:
            self._respond(502, {"error": "reset failed"})

    def _update_firmware(self, idx):
        """Server-side OTA: download the latest ESP-Miner image(s) and flash the
        miner. Body: {"which": "both"|"firmware"|"www"} (default both)."""
        ip, _ = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            body = {}
        which = body.get("which", "both")
        fw = fetch_latest_firmware()
        if not fw:
            return self._respond(502, {"error": "could not fetch latest firmware"})
        results = {}
        try:
            if which in ("both", "firmware") and fw.get("esp_miner_url"):
                results["firmware"] = _flash(ip, "/api/system/OTA",
                                             _download_asset(fw["esp_miner_url"]))
            if which in ("both", "www") and fw.get("www_url"):
                results["www"] = _flash(ip, "/api/system/OTAWWW",
                                        _download_asset(fw["www_url"]))
        except Exception as e:
            return self._respond(502, {"error": str(e), "partial": results})
        self._respond(200, {"ok": bool(results) and all(results.values()),
                            "version": fw.get("version"), "results": results})

    # ── Electricity rates ──────────────────────────────────────────────────────

    def _get_rates(self):
        if rates is None:
            return self._respond(503, {"error": "rates module unavailable"})
        cfg = load_config()
        cfg_rates = cfg.get("rates") or rates.default_rates()
        import datetime
        today = datetime.date.today().isoformat()
        self._respond(200, {
            "config":   cfg_rates,
            "states":   rates.STATE_RATES,
            "resolved": rates.resolve(cfg_rates, today),
        })

    def _set_rates(self):
        if rates is None:
            return self._respond(503, {"error": "rates module unavailable"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length)) if length else {}
            clean = rates.validate(data)
        except (ValueError, KeyError, TypeError) as e:
            return self._respond(400, {"error": str(e)})
        cfg = load_config()
        cfg["rates"] = clean
        save_config(cfg)
        self._respond(200, {"ok": True, "config": clean})

    # ── Overclock benchmark ────────────────────────────────────────────────────

    def _benchmark_start(self, idx):
        if BENCH is None:
            return self._respond(503, {"error": "benchmark module unavailable"})
        ip, info = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            body = {}
        dry = bool(body.get("dry"))
        # Default grid centered on the model's presets if none supplied.
        min_f = int(body.get("min_freq", 450))
        max_f = int(body.get("max_freq", 600))
        step  = int(body.get("step_freq", 25))
        volts = body.get("voltages") or [1100, 1150, 1200]
        plan  = benchmark.build_plan(min_f, max_f, step, volts)
        if not plan:
            return self._respond(400, {"error": "empty plan"})
        ok = BENCH.start(ip, info or {}, plan, dry=dry,
                         settle=body.get("settle"), sample=body.get("sample"))
        if not ok:
            return self._respond(409, {"error": "a benchmark is already running for this miner"})
        self._respond(200, {"started": True, "total": len(plan), "dry": dry})

    def _benchmark_status(self, idx):
        if BENCH is None:
            return self._respond(503, {"error": "benchmark module unavailable"})
        ip, _ = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        st = BENCH.status(ip)
        if not st:
            return self._respond(200, {"running": False, "results": [], "best": None})
        self._respond(200, {
            "running":  st["running"], "dry": st["dry"], "error": st["error"],
            "progress": st["progress"], "current": st["current"],
            "original": st["original"], "results": st["results"],
            "best": st["best"], "applied_best": st["applied_best"],
        })

    def _benchmark_stop(self, idx):
        if BENCH is None:
            return self._respond(503, {"error": "benchmark module unavailable"})
        ip, _ = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        self._respond(200, {"stopped": BENCH.stop(ip)})

    def _benchmark_apply_best(self, idx):
        if BENCH is None:
            return self._respond(503, {"error": "benchmark module unavailable"})
        ip, _ = self._miner_ip_info(idx)
        if ip is None:
            return self._respond(404, {"error": "miner not found"})
        best = BENCH.apply_best(ip)
        if best:
            self._respond(200, {"ok": True, "applied": best})
        else:
            self._respond(404, {"error": "no best result to apply"})

    # ── Low-level ─────────────────────────────────────────────────────────────

    def _proxy(self, url, method="GET", body=None, req=None):
        try:
            if req is None:
                req = urllib.request.Request(url, data=body, method=method)
                if body:
                    req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.URLError as e:
            self._respond(502, {"error": str(e)})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    load_config()
    init_db()
    monitor = push.PushMonitor() if push is not None else None

    def _on_recover(name, params):
        if monitor is not None and getattr(monitor, "enabled", False):
            monitor.apns.send(
                f"\U0001f6df {name} auto-recovered",
                f"It was overheating — reset to {params['frequency']} MHz / "
                f"{params['coreVoltage']} mV and restarted.",
                collapse_id=f"recover-{name}")

    recoverer = recover.AutoRecover(on_action=_on_recover) if recover is not None else None
    t = threading.Thread(target=collector_loop, args=(monitor, recoverer), daemon=True)
    t.start()
    print(f"BitAxe dashboard → http://localhost:{PORT}")
    with http.server.HTTPServer(("", PORT), Handler) as srv:
        srv.serve_forever()
