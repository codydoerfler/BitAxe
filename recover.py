"""Auto-recovery for the BitAxe dashboard.

Watches each poll's miner readings and, when a miner overheats (AxeOS latches
`overheat_mode` and halts the ASIC) or runs dangerously hot, resets it to a
conservative per-model frequency/voltage, hands the fan back to the automatic
curve, and restarts it so it comes back in a safe state.

Rate-limited per miner (cooldown + a daily cap) so a board that keeps faulting
can never be restarted in a tight loop. Pure stdlib (urllib) so it shares the
dashboard's zero-dependency footprint; HTTP failures are swallowed and logged.
"""

import json
import logging
import time
import urllib.request
import urllib.error

log = logging.getLogger("bitaxe.recover")

# Conservative, stability-first frequency (MHz) / core voltage (mV) per ASIC.
# These sit at or just below each board's factory defaults — cool and stable,
# trading a little hashrate for a guaranteed-safe landing state. The miners can
# be re-tuned afterward from the app/dashboard.
SAFE_TUNING = {
    "BM1370": {"frequency": 525, "coreVoltage": 1150},   # Gamma
    "BM1368": {"frequency": 490, "coreVoltage": 1166},   # Supra
    "BM1366": {"frequency": 485, "coreVoltage": 1200},   # Ultra
    "BM1397": {"frequency": 425, "coreVoltage": 1400},   # Max / older
}
DEFAULT_TUNING = {"frequency": 490, "coreVoltage": 1150}

SAFE_TEMP_TARGET = 55      # °C — auto-fan goal after a recovery (runs cooler)
HOT_RECOVER_C    = 70.0    # act at/above this even without an overheat latch
COOLDOWN_S       = 600     # min seconds between recovery actions on one miner
MAX_ACTIONS_DAY  = 12      # hard cap per miner per rolling day (loop guard)


def safe_params(info):
    """Conservative {frequency, coreVoltage} for a miner's ASIC model."""
    model = (info or {}).get("ASICModel") or ""
    return dict(SAFE_TUNING.get(model, DEFAULT_TUNING))


def _patch(ip, body, timeout=10):
    req = urllib.request.Request(
        ip + "/api/system",
        data=json.dumps(body).encode(),
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


def _restart(ip, timeout=10):
    req = urllib.request.Request(ip + "/api/system/restart", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


def apply_safe_reset(ip, info):
    """Reset one miner to conservative tuning + auto fan, then restart it.

    Returns the params applied on success, or None on any failure. Used both by
    the automatic recovery loop and the manual "apply safe fix" API.
    """
    params = safe_params(info)
    body = dict(params)
    body["autofanspeed"] = 1
    body["temptarget"] = SAFE_TEMP_TARGET
    try:
        if not _patch(ip, body):
            return None
        _restart(ip)   # tuning takes effect live; restart clears the overheat latch
        return params
    except (urllib.error.URLError, OSError) as e:
        log.warning("recover: reset %s failed: %s", ip, e)
        return None


class AutoRecover:
    """Diffs each poll and recovers miners that overheat or run too hot."""

    def __init__(self, on_action=None):
        # on_action(name, params) is called after a successful recovery so the
        # caller can push a notification. State: name -> {ts, count, day}.
        self.state = {}
        self.on_action = on_action

    def _allowed(self, name):
        now = time.time()
        st = self.state.get(name, {"ts": 0.0, "count": 0, "day": ""})
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if st["day"] != today:
            st = {"ts": st["ts"], "count": 0, "day": today}
        if now - st["ts"] < COOLDOWN_S:
            return False, st
        if st["count"] >= MAX_ACTIONS_DAY:
            log.warning("recover: %s hit daily action cap (%d)", name, MAX_ACTIONS_DAY)
            return False, st
        return True, st

    def check(self, miners):
        """miners: [{"name", "ip", "online", "info"}]. Acts on overheating
        boards. Returns a list of (name, params) for actions taken this poll."""
        actions = []
        for m in miners:
            info = m.get("info") or {}
            if not m.get("online") or not info:
                continue
            temp     = info.get("temp")
            overheat = bool(info.get("overheat_mode"))
            hot      = isinstance(temp, (int, float)) and temp >= HOT_RECOVER_C
            if not (overheat or hot):
                continue

            name = m["name"]
            allowed, st = self._allowed(name)
            if not allowed:
                continue

            reason = "overheat latch" if overheat else f"{temp:.0f}°C"
            log.warning("recover: %s needs recovery (%s) — applying safe tuning + restart",
                        name, reason)
            params = apply_safe_reset(m["ip"], info)
            now = time.time()
            if params:
                self.state[name] = {"ts": now, "count": st["count"] + 1, "day": st["day"]}
                log.info("recover: %s reset to %dMHz/%dmV and restarted",
                         name, params["frequency"], params["coreVoltage"])
                actions.append((name, params))
                if self.on_action:
                    try:
                        self.on_action(name, params)
                    except Exception as e:
                        log.warning("recover: on_action error: %s", e)
            else:
                # Mark the attempt so a hard-failing miner still respects cooldown.
                self.state[name] = {"ts": now, "count": st["count"] + 1, "day": st["day"]}
        return actions


# ── Self-test (no hardware) ─────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("safe_params BM1370:", safe_params({"ASICModel": "BM1370"}))
    print("safe_params unknown:", safe_params({"ASICModel": "BM9999"}))

    # Stub the network so we can exercise the decision/rate-limit logic offline.
    # Patch this module's own globals (running as __main__).
    calls = []
    globals()["_patch"] = lambda ip, body, timeout=10: calls.append(("patch", ip, body)) or True
    globals()["_restart"] = lambda ip, timeout=10: calls.append(("restart", ip)) or True

    ar = AutoRecover(on_action=lambda n, p: print("  pushed:", n, p))
    hot = [{"name": "t1", "ip": "http://x", "online": True,
            "info": {"ASICModel": "BM1370", "overheat_mode": 1, "temp": 80}}]
    cool = [{"name": "t1", "ip": "http://x", "online": True,
             "info": {"ASICModel": "BM1370", "overheat_mode": 0, "temp": 55}}]
    assert ar.check(hot), "should recover an overheating miner"
    assert not ar.check(hot), "cooldown must block a second immediate action"
    assert not ar.check(cool), "a cool miner needs no action"
    assert any(c[0] == "patch" for c in calls) and any(c[0] == "restart" for c in calls)
    print("self-test OK; calls:", len(calls))
