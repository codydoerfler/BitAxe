"""Electricity-rate resolution for the BitAxe dashboard.

Three modes, configured in config.json under "rates":
  - "tou"    : time-of-use on/off-peak $/kWh, split by summer/winter (the
               original behavior — kept as the default so existing installs
               don't change).
  - "flat"   : one $/kWh for every hour.
  - "region" : pick a US state; use its average residential $/kWh (flat).

A user override always wins — `region` just seeds a sensible flat number from
the state table; the user can switch to `flat`/`tou` and type exact rates.

Approximate 2024 average residential $/kWh by state (EIA-style). These are
ballpark figures for the "set my location" convenience; the override is there
for anyone who wants their exact plan. Pure stdlib.
"""

STATE_RATES = {
    "AL": 0.155, "AK": 0.247, "AZ": 0.142, "AR": 0.123, "CA": 0.314, "CO": 0.150,
    "CT": 0.281, "DE": 0.146, "DC": 0.165, "FL": 0.149, "GA": 0.143, "HI": 0.426,
    "ID": 0.112, "IL": 0.160, "IN": 0.150, "IA": 0.137, "KS": 0.140, "KY": 0.131,
    "LA": 0.121, "ME": 0.230, "MD": 0.177, "MA": 0.305, "MI": 0.190, "MN": 0.149,
    "MS": 0.130, "MO": 0.124, "MT": 0.122, "NE": 0.121, "NV": 0.157, "NH": 0.234,
    "NJ": 0.180, "NM": 0.142, "NY": 0.234, "NC": 0.132, "ND": 0.112, "OH": 0.158,
    "OK": 0.122, "OR": 0.131, "PA": 0.176, "RI": 0.291, "SC": 0.143, "SD": 0.126,
    "TN": 0.130, "TX": 0.151, "UT": 0.114, "VT": 0.215, "VA": 0.145, "WA": 0.110,
    "WV": 0.143, "WI": 0.166, "WY": 0.116,
}

# Original Colorado time-of-use plan — stays the default for backward compat.
DEFAULT_TOU = {
    "summer": {"on": 0.213, "off": 0.079},
    "winter": {"on": 0.184, "off": 0.068},
}
DEFAULT_ON_PEAK_HOURS = [17, 20]   # local 5–9 PM


def default_rates():
    return {"mode": "tou", "tou": DEFAULT_TOU, "on_peak_hours": DEFAULT_ON_PEAK_HOURS}


def on_peak_hours(cfg_rates):
    h = (cfg_rates or {}).get("on_peak_hours") or DEFAULT_ON_PEAK_HOURS
    try:
        lo, hi = int(h[0]), int(h[1])
        return [lo, hi]
    except Exception:
        return DEFAULT_ON_PEAK_HOURS


def resolve(cfg_rates, day_str):
    """Return {"on": $/kWh, "off": $/kWh} for a given 'YYYY-MM-DD'. For flat and
    region modes on == off (no peak split)."""
    cfg_rates = cfg_rates or default_rates()
    mode = cfg_rates.get("mode", "tou")
    if mode == "flat":
        f = float(cfg_rates.get("flat", 0.15))
        return {"on": f, "off": f}
    if mode == "region":
        r = STATE_RATES.get(str(cfg_rates.get("region", "")).upper(), 0.15)
        return {"on": r, "off": r}
    tou = cfg_rates.get("tou", DEFAULT_TOU)
    try:
        month = int(day_str.split("-")[1])
    except Exception:
        month = 1
    season = tou["summer"] if 6 <= month <= 9 else tou["winter"]
    return {"on": float(season["on"]), "off": float(season["off"])}


def validate(incoming):
    """Coerce a user-supplied rates dict into something safe to store. Raises
    ValueError on garbage."""
    if not isinstance(incoming, dict):
        raise ValueError("rates must be an object")
    mode = incoming.get("mode", "tou")
    if mode not in ("tou", "flat", "region"):
        raise ValueError("mode must be tou|flat|region")
    out = {"mode": mode, "on_peak_hours": on_peak_hours(incoming)}
    if mode == "flat":
        out["flat"] = max(0.0, float(incoming.get("flat", 0.15)))
    elif mode == "region":
        region = str(incoming.get("region", "")).upper()
        if region not in STATE_RATES:
            raise ValueError("unknown region")
        out["region"] = region
    else:
        tou = incoming.get("tou", DEFAULT_TOU)
        out["tou"] = {
            "summer": {"on": float(tou["summer"]["on"]), "off": float(tou["summer"]["off"])},
            "winter": {"on": float(tou["winter"]["on"]), "off": float(tou["winter"]["off"])},
        }
    return out


if __name__ == "__main__":
    assert resolve({"mode": "flat", "flat": 0.12}, "2026-07-01") == {"on": 0.12, "off": 0.12}
    assert resolve({"mode": "region", "region": "ca"}, "2026-07-01")["on"] == STATE_RATES["CA"]
    su = resolve(default_rates(), "2026-07-01"); assert su["on"] == 0.213
    wi = resolve(default_rates(), "2026-01-01"); assert wi["on"] == 0.184
    v = validate({"mode": "region", "region": "co"}); assert v["region"] == "CO"
    try:
        validate({"mode": "region", "region": "ZZ"}); assert False
    except ValueError:
        pass
    print("rates self-test OK")
