#!/usr/bin/env python3
"""BitAxe dashboard — static files, API proxy, miner config, history logging."""

import http.server
import urllib.request
import urllib.error
import json
import os
import re
import sqlite3
import threading
import time
from urllib.parse import urlparse, parse_qs

PORT    = 3000
DIR     = os.path.dirname(os.path.abspath(__file__))
CFG     = os.path.join(DIR, "config.json")
DB_PATH = os.path.join(DIR, "history.db")

POLL_INTERVAL = 60  # seconds between readings

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


# ── Background collector ───────────────────────────────────────────────────────

def collector_loop():
    while True:
        cfg = load_config()
        for miner in cfg["miners"]:
            try:
                url = miner["ip"] + "/api/system/info"
                with urllib.request.urlopen(url, timeout=8) as r:
                    data = json.load(r)
                record_reading(miner["ip"], data)
            except Exception:
                pass
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

        # on-peak = weekday (Mon–Fri = strftime %w 1–5), local hour 17–20 (5–9 PM)
        ON = ("CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) BETWEEN 1 AND 5 "
              "AND CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) BETWEEN 17 AND 20")

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
        def rates(day):   # summer = Jun–Sep
            return RATE_SUMMER if 6 <= int(day.split("-")[1]) <= 9 else RATE_WINTER

        days, counts, costs = [], [], []
        for day, p_on, p_off in rows:
            r = rates(day)
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
        r      = rates(today.isoformat())
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
        # Host-health metric for the Mac mini. A real CPU temperature on Apple
        # Silicon needs root/IOKit, so we report CPU load (1-min average as a
        # percent of cores) instead — reliable, no sudo, stdlib only.
        try:
            load1, load5, load15 = os.getloadavg()
            cores = os.cpu_count() or 1
            self._respond(200, {
                "load1":  round(load1, 2),
                "load5":  round(load5, 2),
                "load15": round(load15, 2),
                "cores":  cores,
                "pct":    round(load1 / cores * 100),
            })
        except Exception as e:
            self._respond(503, {"error": str(e)})

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
    load_config()
    init_db()
    t = threading.Thread(target=collector_loop, daemon=True)
    t.start()
    print(f"BitAxe dashboard → http://localhost:{PORT}")
    with http.server.HTTPServer(("", PORT), Handler) as srv:
        srv.serve_forever()
