"""Automated overclock benchmark for the BitAxe dashboard.

Sweeps a miner through a grid of frequency/voltage candidates, lets each settle,
samples hashrate/temp/power, and ranks them by efficiency (with a stability gate
that drops anything that overheats, faults, or under-hashes). Always records the
miner's original settings first and restores them when done (or applies the best
result on request).

Runs server-side in a background thread on the always-on dashboard host, which
sits on the miner's LAN. One run per miner at a time. A `dry` mode simulates the
whole flow without touching hardware, so the UI/plumbing can be tested without
detuning a live miner. Pure stdlib.
"""

import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("bitaxe.benchmark")

SETTLE_S        = 150     # seconds to let a new setting stabilize (real runs)
SAMPLE_S        = 60      # seconds to average readings over (real runs)
SAMPLE_EVERY_S  = 15
TEMP_ABORT_C    = 73.0    # bail on a candidate above this
MIN_HASH_FRAC   = 0.6     # candidate must hit ≥60% of expected hashrate to be "stable"
MAX_CANDIDATES  = 24      # hard cap so a wide grid can't run forever


def build_plan(min_freq, max_freq, step_freq, voltages):
    """Grid of (frequency, coreVoltage) candidates, capped."""
    freqs = list(range(int(min_freq), int(max_freq) + 1, max(5, int(step_freq))))
    plan = [(f, int(v)) for v in voltages for f in freqs]
    return plan[:MAX_CANDIDATES]


def _patch(ip, body, timeout=10):
    req = urllib.request.Request(ip + "/api/system", data=json.dumps(body).encode(),
                                 method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


def _info(ip, timeout=8):
    with urllib.request.urlopen(ip + "/api/system/info", timeout=timeout) as r:
        return json.load(r)


class Benchmarker:
    def __init__(self):
        self.lock = threading.Lock()
        self.runs = {}   # ip -> state dict

    def start(self, ip, info, plan, dry=False, settle=None, sample=None):
        with self.lock:
            st = self.runs.get(ip)
            if st and st.get("running"):
                return False
            self.runs[ip] = {
                "running": True, "dry": dry, "stop": False, "error": None,
                "started": time.time(),
                "settle": (2 if dry else (settle or SETTLE_S)),
                "sample": (1 if dry else (sample or SAMPLE_S)),
                "original": {"frequency": info.get("frequency"),
                             "coreVoltage": info.get("coreVoltage")},
                "expected": info.get("expectedHashrate"),
                "model": info.get("ASICModel"),
                "progress": {"done": 0, "total": len(plan)},
                "current": None, "results": [], "best": None, "applied_best": False,
            }
        threading.Thread(target=self._run, args=(ip, plan), daemon=True).start()
        return True

    def status(self, ip):
        return self.runs.get(ip)

    def stop(self, ip):
        st = self.runs.get(ip)
        if st:
            st["stop"] = True
        return bool(st)

    def apply_best(self, ip):
        st = self.runs.get(ip)
        if not st or not st.get("best"):
            return None
        best = st["best"]
        if st.get("dry"):
            st["applied_best"] = True
            return best
        try:
            _patch(ip, {"frequency": best["frequency"], "coreVoltage": best["coreVoltage"]})
            st["applied_best"] = True
            return best
        except Exception as e:
            log.warning("benchmark: apply_best %s failed: %s", ip, e)
            return None

    # ── internals ────────────────────────────────────────────────────────────

    def _run(self, ip, plan):
        st = self.runs[ip]
        try:
            for (f, v) in plan:
                if st["stop"]:
                    break
                res = self._bench_point(ip, f, v, st)
                if res:
                    st["results"].append(res)
                    self._update_best(st)
                st["progress"]["done"] += 1
            self._restore(ip, st)
        except Exception as e:
            st["error"] = str(e)
            log.warning("benchmark: run %s error: %s", ip, e)
        finally:
            st["current"] = None
            st["running"] = False

    def _bench_point(self, ip, f, v, st):
        st["current"] = {"frequency": f, "coreVoltage": v, "phase": "applying"}
        if st["dry"]:
            return self._simulate(f, v, st)
        try:
            _patch(ip, {"frequency": f, "coreVoltage": v})
        except Exception as e:
            return {"frequency": f, "coreVoltage": v, "stable": False, "note": f"apply failed: {e}"}

        # Settle, watching for an overheat/fault so we don't cook the chip.
        st["current"]["phase"] = "settling"
        deadline = time.time() + st["settle"]
        while time.time() < deadline:
            if st["stop"]:
                return None
            try:
                d = _info(ip)
                if d.get("overheat_mode") or (d.get("power_fault") or "").strip() \
                        or (isinstance(d.get("temp"), (int, float)) and d["temp"] >= TEMP_ABORT_C):
                    return {"frequency": f, "coreVoltage": v, "stable": False,
                            "temp": d.get("temp"), "note": "overheated/faulted"}
            except Exception:
                pass
            time.sleep(min(SAMPLE_EVERY_S, max(1, deadline - time.time())))

        # Sample and average.
        st["current"]["phase"] = "sampling"
        hrs, temps, powers = [], [], []
        end = time.time() + st["sample"]
        while time.time() < end and not st["stop"]:
            try:
                d = _info(ip)
                if d.get("hashRate") is not None: hrs.append(float(d["hashRate"]))
                if d.get("temp") is not None: temps.append(float(d["temp"]))
                if d.get("power") is not None: powers.append(float(d["power"]))
            except Exception:
                pass
            time.sleep(SAMPLE_EVERY_S)
        return self._summarize(f, v, hrs, temps, powers, st.get("expected"))

    def _summarize(self, f, v, hrs, temps, powers, expected):
        def avg(xs): return sum(xs) / len(xs) if xs else None
        hr, temp, power = avg(hrs), avg(temps), avg(powers)
        eff = (hr / power) if hr and power else None
        stable = bool(hr) and (temp is None or temp < TEMP_ABORT_C)
        if expected and hr and hr < expected * MIN_HASH_FRAC:
            stable = False
        return {"frequency": f, "coreVoltage": v, "hashrate": round(hr, 1) if hr else None,
                "temp": round(temp, 1) if temp else None, "power": round(power, 1) if power else None,
                "efficiency": round(eff, 2) if eff else None, "stable": stable}

    def _simulate(self, f, v, st):
        # Plausible model: hashrate ∝ frequency; higher voltage adds heat and
        # power; very high frequency at low voltage goes unstable.
        st["current"]["phase"] = "sampling"
        time.sleep(0.05)
        base = f * 2.05                       # GH/s, BM1370-ish
        headroom = (v - 1100) - (f - 525) * 0.7   # need volts to support freq
        stable = headroom > -40
        hr = base * (1.0 if stable else 0.4)
        power = 14 + f * 0.018 + (v - 1100) * 0.02
        temp = 44 + (f - 450) * 0.05 + (v - 1100) * 0.03
        eff = hr / power
        return {"frequency": f, "coreVoltage": v, "hashrate": round(hr, 1),
                "temp": round(temp, 1), "power": round(power, 1),
                "efficiency": round(eff, 2), "stable": bool(stable and temp < TEMP_ABORT_C)}

    def _update_best(self, st):
        stable = [r for r in st["results"] if r.get("stable") and r.get("efficiency")]
        if stable:
            st["best"] = max(stable, key=lambda r: r["efficiency"])

    def _restore(self, ip, st):
        # Leave the best applied if the caller already did; otherwise restore original.
        if st.get("applied_best"):
            return
        orig = st["original"]
        if st["dry"] or not orig.get("frequency"):
            return
        try:
            _patch(ip, {"frequency": orig["frequency"], "coreVoltage": orig["coreVoltage"]})
        except Exception as e:
            log.warning("benchmark: restore %s failed: %s", ip, e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    b = Benchmarker()
    plan = build_plan(500, 560, 20, [1150, 1200])   # 4 freqs × 2 volts = 8
    assert len(plan) == 8
    info = {"frequency": 700, "coreVoltage": 1220, "ASICModel": "BM1370", "expectedHashrate": 1400}
    assert b.start("http://dry", info, plan, dry=True)
    # wait for the background run to finish
    for _ in range(100):
        st = b.status("http://dry")
        if not st["running"]:
            break
        time.sleep(0.05)
    st = b.status("http://dry")
    assert not st["running"], "run should finish"
    assert st["progress"]["done"] == 8, st["progress"]
    assert st["best"] is not None, "should pick a best"
    assert st["best"]["efficiency"] == max(r["efficiency"] for r in st["results"] if r["stable"])
    print(f"benchmark self-test OK — {len(st['results'])} points, "
          f"best {st['best']['frequency']}MHz/{st['best']['coreVoltage']}mV "
          f"@ {st['best']['efficiency']} GH/W")
