#!/usr/bin/env python3
"""
Vikings Score -> Govee Lights
Flashes your Govee lights purple whenever the Minnesota Vikings score.

No installs needed (Python 3.8+ standard library only).

Usage:
  python3 vikings_lights.py --list      # list your Govee devices (get SKU + device IDs)
  python3 vikings_lights.py --test      # preview all three: Vikings TD, Vikings FG, other team scores
  python3 vikings_lights.py             # watch games and flash on every score

Setup:
  1. Put your Govee API key in GOVEE_API_KEY below (or set env var GOVEE_API_KEY).
  2. Run --list, then copy the sku/device values into DEVICES below.
"""

import json
import os
import sys
import time
import uuid
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

# On GitHub, every repo Variable is passed in as JSON so new settings need no file changes.
try:
    for _k, _v in json.loads(os.environ.get("ALL_VARS") or "{}").items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass


def _num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default

# ======================= CONFIG =======================
GOVEE_API_KEY = os.environ.get("GOVEE_API_KEY", "PASTE-YOUR-KEY-HERE")

# Leave empty to use EVERY Govee light in your account that can change color.
# Or list specific ones (from `--list`) to limit it.
DEVICES = [
    # {"sku": "H6199", "device": "AA:BB:CC:DD:EE:FF:11:22"},
]

# Lights to leave alone (names as shown in the Govee app, comma-separated).
# On GitHub, set this in Settings -> Secrets and variables -> Actions -> Variables -> SKIP_LIGHTS
SKIP_LIGHTS = os.environ.get("SKIP_LIGHTS", "")

# Optional phone alert: install the free "ntfy" app, subscribe to a made-up topic name,
# and put that same name here (on GitHub: Variables -> NTFY_TOPIC). Blank = no alerts.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

TEAM = "MIN"                 # ESPN abbreviation
PURPLE = (79, 38, 131)       # Vikings purple
GOLD = (255, 198, 47)        # Vikings gold
# Adjustable on GitHub: Settings -> Secrets and variables -> Actions -> Variables
FLASH_SECONDS = _num("FLASH_SECONDS", 10)        # how long lights flash for a Vikings TD
FG_FLASH_SECONDS = _num("FG_FLASH_SECONDS", 5)   # for a Vikings field goal / safety
OPPONENT_SECONDS = _num("OPPONENT_SECONDS", 5)   # how long the red pulse lasts
FLASH_SPEED = _num("FLASH_SPEED", 0.8)           # seconds between bright/dim
# Every light flashes, even ones that are off. Set variable FLASH_OFF_LIGHTS to "false"
# to leave lights that are off alone.
FLASH_OFF_LIGHTS = os.environ.get("FLASH_OFF_LIGHTS", "true").strip().lower() == "true"
# Any light with this word in its Govee app name never flashes (rename it in the Govee app).
QUIET_WORD = os.environ.get("QUIET_WORD", "quiet").strip().lower()
# Master switch: set variable LIGHTS_ENABLED to "false" to pause everything.
LIGHTS_ENABLED = os.environ.get("LIGHTS_ENABLED", "true").strip().lower() != "false"

# When the OTHER team scores:
OPPONENT_FLASH = os.environ.get("OPPONENT_FLASH", "true").strip().lower() != "false"
OPPONENT_COLOR = "red"       # "red", or "team" to use the opponent's own team color
RED = (255, 0, 0)

BROADCAST_DELAY = 0          # seconds to wait before flashing (match your TV/stream delay)
FLASH_ON_EXTRA_POINT = False # True = also flash for PAT/2-pt
POLL_LIVE = 8                # seconds between checks during a game
# ======================================================

GOVEE_BASE = "https://openapi.api.govee.com/router/api/v1"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


def http(method, url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def govee(method, path, body=None):
    headers = {"Govee-API-Key": GOVEE_API_KEY, "Content-Type": "application/json"}
    return http(method, GOVEE_BASE + path, body, headers)


def rgb_int(rgb):
    r, g, b = rgb
    return (r << 16) + (g << 8) + b


def control(dev, cap_type, instance, value):
    body = {
        "requestId": str(uuid.uuid4()),
        "payload": {
            "sku": dev["sku"],
            "device": dev["device"],
            "capability": {"type": cap_type, "instance": instance, "value": value},
        },
    }
    try:
        govee("POST", "/device/control", body)
    except urllib.error.HTTPError as e:
        print(f"  Govee error on {dev['device']}: {e.code} {e.read().decode()[:200]}")


def all_lights(fn):
    """Run fn(device) on every light at the same time so they flash together."""
    if not DEVICES:
        return []
    with ThreadPoolExecutor(max_workers=len(DEVICES)) as pool:
        return list(pool.map(fn, DEVICES))


def set_color(rgb):
    all_lights(lambda d: control(d, "devices.capabilities.color_setting", "colorRgb", rgb_int(rgb)))


def set_power(on):
    all_lights(lambda d: control(d, "devices.capabilities.on_off", "powerSwitch", 1 if on else 0))


def load_all_color_lights():
    """Every device on the account that supports RGB color."""
    lights = []
    for d in govee("GET", "/user/devices").get("data", []):
        if any(c.get("instance") == "colorRgb" for c in d.get("capabilities", [])):
            lights.append({"sku": d["sku"], "device": d["device"], "name": d.get("deviceName", "")})
    return lights


def get_state(dev):
    """Best-effort snapshot so we can restore the lights after celebrating."""
    body = {"requestId": str(uuid.uuid4()),
            "payload": {"sku": dev["sku"], "device": dev["device"]}}
    try:
        caps = govee("POST", "/device/state", body)["payload"]["capabilities"]
        state = {}
        for c in caps:
            inst, val = c.get("instance"), c.get("state", {}).get("value")
            if inst in ("powerSwitch", "colorRgb", "brightness") and val not in (None, ""):
                state[inst] = val
        return state
    except Exception:
        return {}


def restore(snapshots):
    all_lights(lambda d: _restore_one(d, snapshots[DEVICES.index(d)]))


def _restore_one(d, s):
        if "colorRgb" in s:
            control(d, "devices.capabilities.color_setting", "colorRgb", s["colorRgb"])
        if "brightness" in s:
            control(d, "devices.capabilities.range", "brightness", s["brightness"])
        if s.get("powerSwitch") == 0:
            control(d, "devices.capabilities.on_off", "powerSwitch", 0)


def set_brightness(level):
    all_lights(lambda d: control(d, "devices.capabilities.range", "brightness", level))


def phone_alert(title, message):
    if not NTFY_TOPIC:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode(),
                                     method="POST", headers={"Title": title})
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"  Phone alert failed: {e}")


def _prepare():
    """Snapshot every light, then limit flashing to the lights that are on (unless told otherwise).
    Returns (all_devices, snapshots) so the caller can put everything back."""
    global DEVICES
    everything = DEVICES
    snapshots = all_lights(get_state)
    if not FLASH_OFF_LIGHTS:
        DEVICES = [d for d, s in zip(everything, snapshots) if s.get("powerSwitch") != 0]
        left_off = len(everything) - len(DEVICES)
        if left_off:
            print(f"  Leaving {left_off} light(s) alone because they're off")
    return everything, snapshots


def _finish(everything, snapshots):
    global DEVICES
    keep = {d["device"] for d in DEVICES}
    restore_pairs = [(d, s) for d, s in zip(everything, snapshots) if d["device"] in keep]
    DEVICES = [d for d, _ in restore_pairs]
    restore([s for _, s in restore_pairs])
    DEVICES = everything


def celebrate(seconds):
    if seconds <= 0:
        return
    print(f"  SKOL! Flashing purple for {seconds:g}s")
    everything, snapshots = _prepare()
    set_power(True)
    set_color(PURPLE)
    end = time.time() + seconds
    while time.time() < end:
        set_brightness(100)
        time.sleep(FLASH_SPEED)
        set_brightness(10)
        time.sleep(FLASH_SPEED)
    set_brightness(100)
    time.sleep(1)
    _finish(everything, snapshots)


def opponent_scored(color=RED):
    if OPPONENT_SECONDS <= 0 or not OPPONENT_FLASH:
        return
    print(f"  Ugh. Red pulse for {OPPONENT_SECONDS:g}s")
    everything, snapshots = _prepare()
    set_power(True)
    set_color(color)
    end = time.time() + OPPONENT_SECONDS
    while time.time() < end:
        set_brightness(100)
        time.sleep(FLASH_SPEED * 1.5)
        set_brightness(15)
        time.sleep(FLASH_SPEED * 1.5)
    time.sleep(1)
    _finish(everything, snapshots)


def hex_to_rgb(h):
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return RED


def find_vikings_game():
    """Returns (state, vikings_score, opponent, short_status, opp_score, opp_color) or None."""
    data = http("GET", ESPN_URL)
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["team"]["abbreviation"]: c for c in comp["competitors"]}
        if TEAM in teams:
            opp = next(a for a in teams if a != TEAM)
            state = ev["status"]["type"]["state"]  # 'pre', 'in', 'post'
            score = int(teams[TEAM].get("score") or 0)
            opp_score = int(teams[opp].get("score") or 0)
            opp_color = hex_to_rgb(teams[opp]["team"].get("color", ""))
            return state, score, opp, ev["status"]["type"]["shortDetail"], opp_score, opp_color
    return None


def list_devices():
    for d in govee("GET", "/user/devices").get("data", []):
        print(f'{{"sku": "{d["sku"]}", "device": "{d["device"]}"}},   # {d.get("deviceName", "")}')


def next_kickoff():
    """Next Vikings kickoff (unix time) from ESPN's schedule, incl. playoffs. None if none left."""
    from datetime import datetime
    base = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{TEAM.lower()}/schedule"
    now = time.time()
    upcoming = []
    for seasontype in (2, 3):  # regular season, postseason
        try:
            events = http("GET", f"{base}?seasontype={seasontype}").get("events", [])
        except Exception:
            continue
        for ev in events:
            completed = ev["competitions"][0]["status"]["type"].get("completed", False)
            t = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).timestamp()
            # include games that started recently and might still be live
            if not completed and t > now - 5 * 3600:
                upcoming.append(t)
    return min(upcoming) if upcoming else None


def sleep_until(ts):
    # sleep in chunks so a computer nap or clock change doesn't throw it off
    while (left := ts - time.time()) > 0:
        time.sleep(min(left, 3600))


def watch_game():
    """Poll every few seconds while the game is live; return when it ends."""
    last_score = last_opp = None
    while True:
        try:
            game = find_vikings_game()
        except Exception as e:
            print(f"ESPN error: {e}")
            time.sleep(30)
            continue

        if not game:
            return
        state, score, opp, status, opp_score, opp_color = game
        if state == "post":
            print(f"Final vs {opp}: MIN {score}")
            return
        if state == "pre":  # pregame / delayed kickoff
            time.sleep(60)
            continue

        if last_score is None:
            print(f"Game on vs {opp}: MIN {score} ({status})")
        elif score > last_score:
            diff = score - last_score
            print(f"MIN scored +{diff} -> {score} ({status})")
            if diff >= 2 or FLASH_ON_EXTRA_POINT:
                time.sleep(BROADCAST_DELAY)
                phone_alert("SKOL!", f"Vikings score! MIN {score} - {opp} {opp_score} ({status})")
                celebrate(FLASH_SECONDS if diff >= 6 else FG_FLASH_SECONDS)
        if last_opp is not None and opp_score > last_opp and OPPONENT_FLASH:
            diff = opp_score - last_opp
            print(f"{opp} scored +{diff} -> {opp_score} ({status})")
            if diff >= 2 or FLASH_ON_EXTRA_POINT:
                time.sleep(BROADCAST_DELAY)
                phone_alert(f"{opp} scored", f"MIN {score} - {opp} {opp_score} ({status})")
                opponent_scored(opp_color if OPPONENT_COLOR == "team" else RED)
        last_score, last_opp = score, opp_score
        time.sleep(POLL_LIVE)


def watch():
    """Sleeps until the next Vikings kickoff, watches the game, repeats."""
    from datetime import datetime
    print("Vikings light watcher running. (Ctrl+C to stop)")
    while True:
        kickoff = next_kickoff()
        if kickoff is None:
            print("No upcoming Vikings games found. Checking again tomorrow.")
            time.sleep(24 * 3600)
            continue
        start = kickoff - 5 * 60  # wake 5 min before kickoff
        if start > time.time():
            print(f"Next game: {datetime.fromtimestamp(kickoff):%a %b %d %I:%M %p}. Sleeping until then.")
            sleep_until(start)
        watch_game()
        time.sleep(60)


if __name__ == "__main__":
    if "PASTE" in GOVEE_API_KEY:
        sys.exit("Add your Govee API key first (GOVEE_API_KEY at the top of the file).")
    if "--list" in sys.argv:
        list_devices()
        sys.exit()
    if not DEVICES:
        DEVICES = load_all_color_lights()
        try:  # list of light names for the control app
            with open("lights.json", "w") as f:
                json.dump(sorted({d["name"] or d["sku"] for d in DEVICES}), f, indent=1)
        except OSError:
            pass
        quiet = [d["name"] for d in DEVICES if QUIET_WORD and QUIET_WORD in d["name"].lower()]
        if quiet:
            DEVICES = [d for d in DEVICES if d["name"] not in quiet]
            print("Quiet (won't flash): " + ", ".join(quiet))
        skip = [x.strip().lower() for x in SKIP_LIGHTS.split(",") if x.strip()]
        if skip:
            skipped = [d["name"] for d in DEVICES if d["name"].strip().lower() in skip]
            DEVICES = [d for d in DEVICES if d["name"].strip().lower() not in skip]
            print("Skipping: " + (", ".join(skipped) or "none matched " + SKIP_LIGHTS))
        print(f"Using all {len(DEVICES)} color lights: " + ", ".join(d["name"] or d["sku"] for d in DEVICES))
    if not DEVICES:
        sys.exit("No color-capable Govee lights found on this account.")
    if not LIGHTS_ENABLED and "--test" not in sys.argv:
        sys.exit("Paused (LIGHTS_ENABLED is false).")
    if "--github" in sys.argv:
        # Run by GitHub every hour: only does anything if a Vikings game starts
        # within the next ~75 min or is already going. Otherwise exits in seconds.
        kickoff = next_kickoff()
        if kickoff is None or kickoff - time.time() > 75 * 60:
            print("No Vikings game soon. Exiting.")
            sys.exit()
        if kickoff - 5 * 60 > time.time():
            sleep_until(kickoff - 5 * 60)
        watch_game()
        sys.exit()
    if "--test" in sys.argv:
        phone_alert("SKOL!", "Test alert: this is what a Vikings score looks like")
        print("Vikings touchdown...")
        celebrate(FLASH_SECONDS)
        time.sleep(3)
        print("Vikings field goal...")
        celebrate(FG_FLASH_SECONDS)
        time.sleep(3)
        print("Other team scores...")
        opponent_scored()
    else:
        try:
            watch()
        except KeyboardInterrupt:
            print("\nBye.")
