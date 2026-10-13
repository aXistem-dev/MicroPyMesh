"""View models for the screens: what each row and bubble says, in plain Python.

No LVGL here, so it runs (and is tested) on the desktop; the screens only turn these
dicts and strings into widgets. Times are Unix seconds; `tz_s` is the local offset.
"""

import time

import meshcore_presets

# Colours (0xRRGGBB) of the design canvas, shared with ui_theme.
BG = 0x10151B         # app background; text on accent fills
BAR = 0x0D1217        # tab bar, composer bar
SURFACE = 0x19212A    # chips, pills, cards, incoming bubbles, inputs
SURFACE2 = 0x222C37   # pressed state
LINE = 0x222C37       # dividers
OUTLINE = 0x2C3846    # outlines, secondary buttons
TEXT = 0xE9EEF3
MUTED = 0xA3B0BD
ACCENT = 0xF4A93B
OWN = 0x1E4B78        # outgoing bubble
OWN_TEXT = 0xF2F7FC
CHAN_BG, CHAN_FG = 0x1F3A3F, 0x8FD3DA
DM_BG, DM_FG = 0x3A2E52, 0xCDB8F5
ROOM_BG = 0x2B3542
DELIVERED = 0x6CB4FF  # delivered check
FAIL = 0xFF9F7A       # failed bubble border
FAIL_TEXT = 0xFFB89C  # failed meta line
OK = 0x9BE0A8         # fresh age
WARN = 0xF2C98A       # older age, unheard
ERR = FAIL_TEXT
SENDER_COLORS = (CHAN_FG, DM_FG, 0xF2C98A, 0x9BE0A8, 0x6CB4FF, 0xFFB89C)
ON_ACCENT = BG        # text and icons on accent fills
KEYBOARD = 0x0A0E13
PIN_RPTR, PIN_ROOM = 0x6CB4FF, 0x9BE0A8
# These are the dark palette; ui_theme.apply() swaps in the one in use (ui_palette).

MAX_TEXT_LEN = 160          # bytes of text in one MeshCore message (BaseChatMesh.h)
QUICK_REPLIES = ("copy", "on my way", "ETA 10 min", "signal report")

_EPOCH_2000 = 946684800
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_KINDS = {1: "chat", 2: "rptr", 3: "room", 4: "sensor"}


def _gmtime(unix_s):
    """time.gmtime() for Unix seconds, also on ports whose epoch is 2000."""
    if time.gmtime(0)[0] == 2000:
        unix_s -= _EPOCH_2000
    return time.gmtime(int(unix_s))


def _minus(s):
    return s.replace("-", "−")


def snr_text(snr):
    return _minus("%.1f" % snr)


def time_text(ts, now_s, tz_s=0):
    """'14:31' today, 'Wed' this week, '12 Sep' before that."""
    t = _gmtime(ts + tz_s)
    n = _gmtime(now_s + tz_s)
    if t[0:3] == n[0:3]:
        return "%02d:%02d" % (t[3], t[4])
    if 0 <= now_s - ts < 6 * 86400:
        return _DAYS[t[6]]
    return "%d %s" % (t[2], _MONTHS[t[1] - 1])


_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def day_number(ts, tz_s=0):
    """The local day `ts` falls on, as a day count: equal for times on the same date."""
    return (ts + tz_s) // 86400


def day_text(ts, now_s, tz_s=0):
    """A thread's day divider: 'Today', 'Yesterday', a weekday this week, '12 Sep', or
    '28 Aug 2025' in another year."""
    ago = day_number(now_s, tz_s) - day_number(ts, tz_s)
    if ago == 0:
        return "Today"
    if ago == 1:
        return "Yesterday"
    t = _gmtime(ts + tz_s)
    if 1 < ago < 7:
        return _DAY_NAMES[t[6]]
    if t[0] != _gmtime(now_s + tz_s)[0]:
        return "%d %s %d" % (t[2], _MONTHS[t[1] - 1], t[0])
    return "%d %s" % (t[2], _MONTHS[t[1] - 1])


def tz_offset(local_tuple, utc_tuple):
    """Seconds the local clock is ahead of UTC, from the same instant read both ways
    (time tuples). Offsets are taken to lie within -12 h .. +14 h."""
    mins = (local_tuple[3] * 60 + local_tuple[4]) - (utc_tuple[3] * 60 + utc_tuple[4])
    while mins > 14 * 60:
        mins -= 24 * 60
    while mins < -12 * 60:
        mins += 24 * 60
    return mins * 60


def clock_text(ts, tz_s=0):
    t = _gmtime(ts + tz_s)
    return "%02d:%02d" % (t[3], t[4])


_emoji_known = None     # cp -> bool, from MicroPythonOS's emoji set (ui_theme sets it)


def set_emoji_filter(known):
    """known(codepoint) -> True when the device has an image for that emoji; None keeps all."""
    global _emoji_known
    _emoji_known = known


def _is_pictograph(cp):
    # Emoji blocks, without the regional indicators (flags, 1F1E6-1F1FF) and the
    # modifiers, which the emoji font already draws or hides itself.
    if 0x1F1E6 <= cp <= 0x1F1FF or 0x1F3FB <= cp <= 0x1F3FF:
        return False
    return 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF


def display(text):
    """Text as the screen can show it: emoji the device has no image for are dropped
    (the text font would draw them as empty boxes); everything else is kept."""
    if not text or _emoji_known is None:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if _is_pictograph(cp) and not _emoji_known(cp):
            continue
        out.append(ch)
    return "".join(out).strip() if len(out) != len(text) else text


def initials(name):
    # The avatar font has no emoji: leave out pictographs and flag letters.
    name = "".join(ch for ch in (name or "")
                   if not _is_pictograph(ord(ch)) and not 0x1F1E6 <= ord(ch) <= 0x1F1FF
                   and ord(ch) not in (0xFE0F, 0x200D))
    words = (name or "?").split() or ["?"]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    return words[0][:2].upper()


def _preview(msg, dm):
    if msg is None:
        return "No messages yet"
    if not msg.get("incoming"):
        return "You: " + display(msg.get("text", ""))
    if dm:
        return display(msg.get("text", ""))
    return "%s: %s" % (display(msg.get("sender", "?")), display(msg.get("text", "")))


def chat_rows(mgr, now_s, filt="all", tz_s=0):
    """Rows for the Chats tab, newest activity first. filt: all, unread, direct, channels."""
    rows = []
    if filt != "direct":
        for name in mgr.get_channel_names():
            msgs = mgr.get_messages(name)
            last = msgs[-1] if msgs else None
            rows.append({"key": name, "kind": "channel", "title": display(name), "initials": "#",
                         "last": last})
    if filt != "channels":
        for c in mgr.get_dm_chats():   # contacts and former contacts with a chat kept
            ctype = c.get("type", 1)
            msgs = mgr.get_dm_messages(c["pubkey"])
            last = msgs[-1] if msgs else None
            name = display(c.get("name")) or c["id"]
            rows.append({"key": c["pubkey"], "kind": "room" if ctype == 3 else "dm",
                         "title": name, "initials": initials(name), "last": last})
    for r in rows:
        last = r.pop("last")
        r["ts"] = last.get("ts", 0) if last else 0
        r["preview"] = _preview(last, r["kind"] == "dm")
        if r["kind"] == "room" and last and last.get("incoming") and last.get("sender"):
            r["preview"] = "%s: %s" % (display(last["sender"]), display(last.get("text", "")))
        r["time"] = time_text(r["ts"], now_s, tz_s) if last else ""
        r["unread"] = mgr.get_unread(r["key"])
        r["mention"] = mgr.get_mention(r["key"])
    if filt == "unread":
        rows = [r for r in rows if r["unread"]]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def delivery(msg, tz_s=0):
    """Status line of an outgoing message: {icon (None/"check"/"retry"), icon_color, text,
    color, dots (repeater echoes shown as dots, at most 6)}."""
    t = clock_text(msg.get("ts", 0), tz_s)
    if msg.get("failed"):
        return {"icon": "retry", "text": "no ack after 4 tries · tap to resend",
                "color": FAIL_TEXT, "dots": 0, "icon_color": FAIL_TEXT}
    if msg.get("unheard") and not msg.get("heard"):
        return {"icon": None, "text": "not heard by a repeater · tap to resend",
                "color": WARN, "dots": 0}
    if not msg.get("tx"):
        return {"icon": None, "text": t + " · sending", "color": MUTED, "dots": 0}
    if msg.get("delivered"):
        text = "delivered " + clock_text(msg.get("ack_ts") or msg.get("ts", 0), tz_s)
        if msg.get("ack_snr") is not None:
            text += " · SNR " + snr_text(msg["ack_snr"])
        return {"icon": "check", "text": text, "color": MUTED, "dots": 0, "icon_color": DELIVERED}
    if msg.get("heard"):
        return {"icon": None, "text": "%s · heard ×%d" % (t, msg["heard"]), "color": MUTED,
                "dots": min(msg["heard"], 6)}
    return {"icon": None, "text": t + " · sent", "color": MUTED, "dots": 0}


def sender_color(name):
    """A stable colour per sender name in channel threads."""
    h = 0
    for ch in name or "":
        h = (h * 31 + ord(ch)) & 0xFFFF
    return SENDER_COLORS[h % len(SENDER_COLORS)]


def can_resend(msg):
    return not msg.get("incoming") and bool(msg.get("failed") or msg.get("unheard"))


def budget(text, channel_name=None, sender=None):
    """Bytes left in a message (negative = too long). A channel message carries
    'sender: ' in front of the text."""
    used = len((text or "").encode("utf-8"))
    if channel_name:
        used += len((sender or "").encode("utf-8")) + 2
    return MAX_TEXT_LEN - used


def age_text(seconds):
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return "%d min" % (seconds // 60)
    if seconds < 86400:
        return "%d h" % (seconds // 3600)
    return "%d d" % (seconds // 86400)


def _ticks_age_s(now_ms, then_ms):
    """Seconds between two ticks_ms() values, across the 2**30 ms wrap."""
    half = 1 << 29
    return max(0, ((now_ms - then_ms + half) % (1 << 30) - half) // 1000)


def _age_color(seconds):
    if seconds < 30 * 60:
        return OK
    if seconds < 6 * 3600:
        return WARN
    return MUTED


def distance_text(metres):
    if metres < 1000:
        return "%d m" % round(metres)
    if metres < 10000:
        return "%.1f km" % (metres / 1000)
    return "%d km" % round(metres / 1000)


def metres_between(lat1, lon1, lat2, lon2):
    """Distance between two points (equirectangular: close enough within a mesh)."""
    import math
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.sqrt(x * x + y * y)


def _located(n):
    """Has a position; 0, 0 is what nodes without one advertise (as on the map)."""
    lat, lon = n.get("lat"), n.get("lon")
    return lat is not None and lon is not None and not (lat == 0 and lon == 0)


SORTS = (("heard", "Last heard"), ("name", "Name"), ("nearest", "Nearest"),
         ("message", "Last message"))


def node_rows(nodes, now_ms, filt="all", contacts=(), query="", sort="heard", here=None,
              last_msg=None):
    """Rows for the Nodes tab. filt: all, contacts (saved), chat, rptr, room, new (heard in
    the last hour and not a contact), located (has a position), fav (starred); query matches the name or
    hex id, any case. `nodes` come most recent first, as the manager lists them. sort:
    heard (that order), name, nearest (to `here`, (lat, lon); unplaced ones last) or
    message (`last_msg` {pubkey: ts}, newest first). With `here` the meta line ends in the
    distance. Favourites come first."""
    rows = []
    q = (query or "").strip().lower()
    for i, n in enumerate(nodes):
        kind = _KINDS.get(n.get("type"), "other")
        age_s = _ticks_age_s(now_ms, n.get("heard_ms", now_ms))
        if filt == "new":
            if age_s >= 3600 or n.get("pubkey") in contacts:
                continue
        elif filt == "contacts":
            if n.get("pubkey") not in contacts:
                continue
        elif filt == "located":
            if not _located(n):
                continue
        elif filt == "fav":
            if not n.get("fav"):
                continue
        elif filt != "all" and kind != filt:
            continue
        if q and q not in (n.get("name") or "").lower() and not (n.get("id") or "").lower().startswith(q):
            continue
        hops = n.get("hops") or 0
        meta = "direct" if hops == 0 else ("1 hop" if hops == 1 else "%d hops" % hops)
        if n.get("snr") is not None:
            meta += " · SNR " + snr_text(n["snr"])
        heard = "heard_ms" in n
        if not heard:
            meta = "not heard yet"
        dist = None
        if here is not None and _located(n):
            dist = metres_between(here[0], here[1], n["lat"], n["lon"])
            meta += " \u00b7 " + distance_text(dist)
        rows.append({"pubkey": n.get("pubkey"), "hex": (n.get("id") or n.get("pubkey", "??")[:2]).upper(),
                     "kind": kind, "name": display(n.get("name")) or "?",
                     "age": age_text(age_s) if heard else "",
                     "age_color": _age_color(age_s), "meta": meta,
                     "contact": n.get("pubkey") in contacts, "fav": bool(n.get("fav")),
                     "_i": i, "_d": dist})
    # Favourites first. MicroPython's sort is not stable: the position in `nodes` breaks
    # every tie.
    if sort == "name":
        rows.sort(key=lambda r: (not r["fav"], r["name"].lower(), r["_i"]))
    elif sort == "nearest" and here is not None:
        rows.sort(key=lambda r: (not r["fav"], r["_d"] is None, r["_d"] or 0, r["_i"]))
    elif sort == "message":
        last = last_msg or {}
        rows.sort(key=lambda r: (not r["fav"], -last.get(r["pubkey"], -1), r["_i"]))
    else:
        rows.sort(key=lambda r: (not r["fav"], r["_i"]))
    for r in rows:
        del r["_i"], r["_d"]
    return rows


_KIND_WORDS = {"chat": "companion", "rptr": "repeater", "room": "room server", "sensor": "sensor"}


def _via(path_hex, hops):
    """'3af1', 2 hops -> '3A › F1' (each repeater's hash prefix, in order)."""
    if not path_hex or not hops or len(path_hex) % hops:
        return ""
    step = len(path_hex) // hops
    return " › ".join(path_hex[i:i + step].upper() for i in range(0, len(path_hex), step))


def node_detail(n, now_ms):
    """Header, route line (lead, mono path, tail) and key-value fields for a node's
    detail screen."""
    kind = _KINDS.get(n.get("type"), "other")
    pk = (n.get("pubkey") or "").upper()
    hops = n.get("hops") or 0
    lead = "direct" if hops == 0 else ("1 hop" if hops == 1 else "%d hops" % hops)
    via = _via(n.get("path"), hops)
    tail = ""
    if n.get("snr") is not None:
        tail += " · SNR %s dB" % snr_text(n["snr"])
    age = age_text(_ticks_age_s(now_ms, n.get("heard_ms", now_ms)))
    tail += " · heard now" if age == "now" else " · heard %s ago" % age
    fields = [("Type", _KIND_WORDS.get(kind, kind)), ("Hops", str(hops))]
    if n.get("snr") is not None:
        fields.append(("Last SNR", snr_text(n["snr"]) + " dB"))
    if n.get("rssi") is not None:
        fields.append(("Last RSSI", _minus("%d" % n["rssi"]) + " dBm"))
    if n.get("lat") is not None and n.get("lon") is not None:
        fields.append(("Location", "%.4f, %.4f" % (n["lat"], n["lon"])))
    fields.append(("Signature", "verified" if n.get("verified") else "not checked"))
    return {"title": display(n.get("name")) or pk[:8] or "?",
            "subtitle": "%s · %s…%s" % (_KIND_WORDS.get(kind, kind), pk[:4], pk[-4:]),
            "info": lead + tail,
            "route": (lead + " via " if via else lead, via, tail),
            "fields": fields, "pubkey": pk.lower()}


def radio_texts(st):
    """Strings for the Radio tab from manager.radio_stats()."""
    if not st.get("rx_on"):
        sub = "RX off"
    elif st.get("last_rx_s") is None:
        sub = "RX on · nothing heard yet"
    else:
        s = st["last_rx_s"]
        sub = "RX on · last packet %s ago" % ("%d s" % s if s < 60 else age_text(s))
    noise = st.get("noise_dbm")
    peak = st.get("peak_rssi_30m")
    return {"subtitle": sub,
            "noise": _minus("%d" % noise) if noise is not None else "—",
            "stats": [("Peak", _minus("%d" % peak) if peak is not None else "—"),
                      ("Packets", "%d/h" % st.get("packets_per_h", 0)),
                      ("TX air", "%.1f %%" % st.get("tx_air_pct", 0.0))]}


def _minutes(m):
    return "%d h" % (m // 60) if m >= 60 and m % 60 == 0 else "%d min" % m


def auto_advert_text(cfg):
    """The Settings value for automatic adverts: "Off", "12 h", "nearby 30 min", or both."""
    parts = []
    if cfg["flood_h"]:
        parts.append("%d h" % cfg["flood_h"])
    if cfg["zero_hop_min"]:
        parts.append("nearby " + _minutes(cfg["zero_hop_min"]))
    return " \u00b7 ".join(parts) or "Off"


def hhmm(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def quiet_text(q):
    """The Settings value for quiet hours: "Off" or "22:00–07:00"."""
    return "%s\u2013%s" % (hhmm(q["start"]), hhmm(q["end"])) if q["enabled"] else "Off"


def preset_summary(p, power):
    """(title, detail, airtime) for the Radio tab's preset card."""
    title = p.get("name") or "Custom"
    detail = "%s · %d dBm" % (meshcore_presets.describe(p), power)
    air = meshcore_presets.airtime_ms(p, 40) / 1000
    return (title, detail, "≈ %.2f s on air per 40-byte message" % air)


def signal_report(msg):
    hops = msg.get("hops") or 0
    s = "SNR " + snr_text(msg["snr"]) if msg.get("snr") is not None else "no SNR"
    return "%s · %d hop%s" % (s, hops, "" if hops == 1 else "s")


# --- repeaters and room servers ---------------------------------------------- #

def _thousands(n):
    s = "%d" % n
    out = ""
    while len(s) > 3:
        out = " " + s[-3:] + out
        s = s[:-3]
    return s + out


def uptime_text(seconds):
    d, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    if d:
        return "%d d %d h" % (d, h)
    if h:
        return "%d h %d min" % (h, m)
    return "%d min" % m


def status_rows(data, room=False):
    """(label, value) pairs for a server's status, in the canvas's order; missing fields
    (older firmware) are left out."""
    rows = []
    up = data.get("uptime_s")
    if data.get("battery_mv") is not None:
        rows.append(("Battery", "%.2f V" % (data["battery_mv"] / 1000)))
    if up is not None:
        rows.append(("Uptime", uptime_text(up)))
    if data.get("noise_floor") is not None:
        rows.append(("Noise floor", _minus("%d" % data["noise_floor"]) + " dBm"))
    if data.get("last_snr") is not None:
        rows.append(("Last SNR", snr_text(data["last_snr"]) + " dB"))
    for key, label in (("airtime_s", "TX airtime"), ("rx_airtime_s", "RX airtime")):
        if data.get(key) is not None and up:
            rows.append((label, "%.1f %%" % (data[key] * 100 / up)))
    if data.get("packets_rx") is not None:
        rows.append(("Packets RX", _thousands(data["packets_rx"])))
    if data.get("packets_tx") is not None:
        rows.append(("Packets TX", _thousands(data["packets_tx"])))
    if room and data.get("posted") is not None:
        rows.append(("Posts", _thousands(data["posted"])))
    return rows


def neighbour_rows(rows, nodes):
    """Neighbour list entries with names where we know the node (from its advert)."""
    out = []
    for r in rows:
        name = None
        for pk, n in nodes.items():
            if pk.startswith(r["prefix"]):
                name = display(n.get("name"))
                break
        out.append({"name": name or r["prefix"].upper(), "hex": r["prefix"][:2].upper(),
                    "detail": "SNR %s \u00b7 %s ago" % (snr_text(r["snr"]), age_text(r["secs_ago"]))})
    return out


_TELEMETRY = {"voltage": ("Voltage", "%.2f V"), "temperature": ("Temperature", "%.1f \u00b0C"),
              "humidity": ("Humidity", "%.1f %%"), "pressure": ("Pressure", "%.1f hPa"),
              "illuminance": ("Light", "%d lx"), "current": ("Current", "%.3f A"),
              "percentage": ("Level", "%d %%"), "power": ("Power", "%d W"),
              "concentration": ("Concentration", "%d ppm"), "altitude": ("Altitude", "%d m"),
              "analog_in": ("Analog", "%.2f")}


def telemetry_rows(rows):
    out = []
    for r in rows:
        if r["kind"] == "gps":
            label, value = "Location", "%.4f, %.4f" % (r["value"][0], r["value"][1])
        else:
            label, fmt = _TELEMETRY.get(r["kind"], (cap(r["kind"].replace("_", " ")), "%s"))
            value = fmt % r["value"]
        if r["channel"] != 1:
            label = "%s \u00b7 %d" % (label, r["channel"])
        out.append((label, value.replace("-", "\u2212") if value.startswith("-") else value))
    return out


def cap(s):
    """First letter upper case (MicroPython's str has no capitalize())."""
    return s[:1].upper() + s[1:]


def _duration(ms):
    return "%d ms" % ms if ms < 1000 else "%.1f s" % (ms / 1000)


def trace_text(result, kind):
    snrs = result.get("hop_snrs") or []
    if kind == "ping":
        there = snr_text(snrs[0]) if snrs else "?"
        back = snr_text(result["final_snr"]) if result.get("final_snr") is not None else "?"
        return "Ping %s \u00b7 SNR there %s \u00b7 back %s" % (_duration(result.get("rtt_ms", 0)), there, back)
    hops = ["%02X %s" % (h & 0xFF, snr_text(snrs[i]) if i < len(snrs) else "?")
            for i, h in enumerate(result.get("hashes") or [])]
    if result.get("final_snr") is not None:
        hops.append("you %s" % snr_text(result["final_snr"]))
    return "%s \u00b7 %s" % (" \u203a ".join(hops), _duration(result.get("rtt_ms", 0)))


def login_line(session):
    """(text, colour) for the login row."""
    state = session.get("state")
    if state == "pending":
        if (session.get("pending") or {}).get("auto") and session.get("error"):
            return (session["error"], WARN)
        return ("Logging in\u2026", MUTED)
    if state == "ok":
        role = session.get("role") or "guest"
        return ("%s login" % cap(role.replace("-", " ")), OK if role == "admin" else MUTED)
    if state == "failed":
        return (session.get("error") or "Login failed", FAIL_TEXT)
    return ("Not logged in", MUTED)


def clock_warning(session):
    """A line under the login when the server's clock is far off ours, else None."""
    skew = session.get("clock_skew_s") if session.get("state") == "ok" else None
    if not skew:
        return None
    return ("Its clock is %s %s ours. Logins and the order of posts can go wrong until "
            "one of the clocks is set right." % (age_text(abs(skew)),
                                                 "ahead of" if skew > 0 else "behind"))


def recent_rows(recent):
    """Rows for the Radio tab's "recently heard": (age, kind, RSSI, SNR, hops)."""
    out = []
    for r in recent:
        a = r["age_s"]
        age = "now" if a < 60 else ("%dm" % (a // 60) if a < 3600 else "%dh" % (a // 3600))
        rssi = _minus("%d" % r["rssi"]) + " dBm" if r.get("rssi") is not None else "\u2014"
        snr = snr_text(r["snr"]) if r.get("snr") is not None else "\u2014"
        h = r.get("hops") or 0
        hops = "direct" if h == 0 else ("1 hop" if h == 1 else "%d hops" % h)
        out.append((age, r["kind"], rssi, snr, hops))
    return out


def rx_rate_text(per_min):
    return "~%d/min" % per_min if per_min >= 1 else "~%.1f/min" % per_min


# --- own position --------------------------------------------------------- #

def parse_coords(text):
    """(lat, lon) from typed text: "50.8279, 3.2649", "50.8279 3.2649" or with decimal commas
    "50,8279; 3,2649" / "50,8279 3,2649". None when it is not two numbers."""
    t = text.strip()
    if ";" in t:
        parts = t.split(";")
    elif t.count(",") == 1 and "." in t or t.count(",") == 1 and " " not in t:
        parts = t.split(",")
    else:
        parts = t.split()
    parts = [p.strip().replace(",", ".") for p in parts if p.strip()]
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None


_SOURCES = {"manual": "set by hand", "gps": "from the GPS"}
_GPS_STATES = {"off": "Off", "absent": "No GPS found, switched off",
               "searching": "Looking for the GPS…", "no_fix": "Waiting for a fix",
               "fix": "Position from the GPS"}


def position_text(pos):
    """(coordinates, where they came from) for the Location page."""
    if not pos:
        return "Not set", ""
    return "%.5f, %.5f" % (pos["lat"], pos["lon"]), _SOURCES.get(pos.get("source"), "")


def gps_text(status):
    return _GPS_STATES.get(status["state"], status["state"])


# --- routes and message details --------------------------------------------- #

def route_pill(mode, path_raw, has_path):
    """The DM header's route: flood, direct or N hops; (forced) / (set) when chosen."""
    if mode == "flood":
        return "flood (forced)"
    if not has_path:
        return "flood"
    hops = (path_raw or 0) & 63
    text = "direct" if hops == 0 else ("1 hop" if hops == 1 else "%d hops" % hops)
    return text + " (set)" if mode == "manual" else text


def hops_text(path_hex, size):
    """"A1 → B2 → C3": a path's hops, `size` bytes each."""
    step = 2 * (size or 1)
    return " → ".join(path_hex[i:i + step].upper() for i in range(0, len(path_hex), step))


def path_hops(path_hex, size, nodes=()):
    """[(hop hex, name or None)]: each hop with the node whose key starts with it, when
    exactly one known node does."""
    step = 2 * (size or 1)
    out = []
    for i in range(0, len(path_hex), step):
        h = path_hex[i:i + step].lower()
        hits = [n for n in nodes if (n.get("pubkey") or "").lower().startswith(h)]
        out.append((h.upper(), display(hits[0].get("name")) or None if len(hits) == 1 else None))
    return out


def _when(ts, tz_s):
    t = _gmtime(ts + tz_s)
    return "%s %d %s %02d:%02d" % (_DAYS[t[6]], t[2], _MONTHS[t[1] - 1], t[3], t[4])


def message_details(msg, tz_s=0, nodes=()):
    """[(label, value)] for the message details page; `nodes` name the path's hops."""
    rows = []
    if msg.get("incoming"):
        rows.append(("From", msg.get("sender") or "?"))
    rows.append(("Sent", _when(msg.get("ts", 0), tz_s)))
    if msg.get("incoming"):
        hops = msg.get("hops")
        if hops is not None:
            rows.append(("Hops", "0 (direct)" if hops == 0 else str(hops)))
        if msg.get("path"):
            hops_named = path_hops(msg["path"], msg.get("hsize", 1), nodes)
            rows.append(("Path", " \u2192 ".join(h + (" " + n if n else "")
                                                  for h, n in hops_named)))
            size = msg.get("hsize", 1)
            rows.append(("Path hash", "%d byte%s" % (size, "" if size == 1 else "s")))
        elif hops:
            rows.append(("Path", "not recorded"))
        if msg.get("region"):
            rows.append(("Region", "unknown" if msg["region"] == "?" else "#" + msg["region"]))
        if msg.get("snr") is not None:
            rows.append(("SNR", snr_text(msg["snr"]) + " dB"))
        if msg.get("rssi") is not None:
            rows.append(("RSSI", _minus("%d" % msg["rssi"]) + " dBm"))
    else:
        if msg.get("failed"):
            status = "no ack after 4 tries"
        elif msg.get("delivered"):
            status = "delivered " + clock_text(msg.get("ack_ts") or msg.get("ts", 0), tz_s)
        elif msg.get("heard"):
            n = msg["heard"]
            status = "heard by %d repeater%s" % (n, "" if n == 1 else "s")
        elif msg.get("unheard"):
            status = "not heard by a repeater"
        else:
            status = "sent" if msg.get("tx") else "sending"
        rows.append(("Status", status))
    return rows


# --- links in messages ---------------------------------------------------------- #

_TRAIL = ".,;:!?)]}>'\""
_LEAD = "([{<'\""
_MAX_LINKS = 3


def _num_at(t, i):
    """A decimal number (-12.345) starting at t[i]: (value, decimals, end) or None. It must
    stand on its own: no digit, letter or dot right before or after it."""
    n = len(t)
    if i > 0 and (t[i - 1].isalpha() or t[i - 1].isdigit() or t[i - 1] in ".-"):
        return None
    j = i + 1 if i < n and t[i] == "-" else i
    k = j
    while k < n and t[k].isdigit():
        k += 1
    if k == j or k >= n or t[k] != ".":
        return None
    d = k + 1
    while d < n and t[d].isdigit():
        d += 1
    if d == k + 1 or (d < n and (t[d] == "." or t[d].isalpha() or t[d].isdigit())):
        return None
    return float(t[i:d]), d - k - 1, d


def _pair_at(t, i, min_decimals):
    """'lat, lon' starting at t[i]: (lat, lon, end) or None."""
    a = _num_at(t, i)
    if a is None or a[1] < min_decimals:
        return None
    j = a[2]
    while j < len(t) and t[j] == " ":
        j += 1
    if j >= len(t) or t[j] != ",":
        return None
    j += 1
    while j < len(t) and t[j] == " ":
        j += 1
    b = _num_at(t, j)
    if b is None or b[1] < min_decimals:
        return None
    if not (-90 <= a[0] <= 90 and -180 <= b[0] <= 180):
        return None
    return a[0], b[0], b[2]


def _hashtag(tok):
    name = tok[1:].lower()
    if (len(tok) < 2 or tok[0] != "#" or name.isdigit()
            or not all(c.isalpha() or c.isdigit() or c in "-_" for c in name)):
        return None
    return "#" + name


def message_links(text):
    """What can be tapped in a message, in order (at most three): meshcore:// contact and
    channel links, #hashtag channels, and places (geo:, a maps link's q=, or a bare
    'lat, lon' with at least three decimals)."""
    from meshcore_advert import parse_contact_text, parse_channel_uri
    t = text or ""
    out = []
    places = set()

    def place(lat, lon):
        if (lat, lon) not in places:
            places.add((lat, lon))
            out.append({"kind": "map", "lat": lat, "lon": lon})

    for tok in t.split():
        while tok and tok[0] in _LEAD:
            tok = tok[1:]
        while tok and tok[-1] in _TRAIL:
            tok = tok[:-1]
        if tok.startswith("meshcore://contact/add?"):
            got = parse_contact_text(tok)
            if got is not None:
                out.append({"kind": "contact", "uri": tok, "name": got[1], "key": got[0],
                            "type": got[2]})
        elif tok.startswith("meshcore://channel/add?"):
            got = parse_channel_uri(tok)
            if got is not None:
                out.append({"kind": "channel", "uri": tok, "name": got[0]})
        elif tok.startswith("#"):
            tag = _hashtag(tok)
            if tag is not None:
                out.append({"kind": "hashtag", "name": tag})
        else:
            for marker in ("geo:", "q=", "ll=", "query="):
                at = tok.find(marker)
                if at >= 0 and (marker == "geo:" or at == 0 or tok[at - 1] in "?&"):
                    got = _pair_at(tok, at + len(marker), 1)
                    if got is not None:
                        place(got[0], got[1])
                    break
    i = 0
    while i < len(t):
        got = _pair_at(t, i, 3)
        if got is not None:
            place(got[0], got[1])
            i = got[2]
        else:
            i += 1
    return out[:_MAX_LINKS]


def _coord(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def link_text(link, known):
    """A link chip's text; `known`: the contact or channel is already ours."""
    kind = link["kind"]
    if kind == "map":
        return "Map %s, %s" % (_coord(link["lat"]), _coord(link["lon"]))
    if kind == "contact":
        name = display(link.get("name")) or link["key"][:8].upper()
    else:
        name = link["name"]
    verb = "Open" if known else ("Add" if kind == "contact" else "Join")
    return "%s %s" % (verb, name)


# --- emoji picker ------------------------------------------------------------------ #

# Offered first when the system has them: replies and mesh talk.
COMMON_EMOJI = ("\U0001F44D", "\u2764", "\U0001F602", "\U0001F60A", "\U0001F642",
                "\U0001F62E", "\U0001F622", "\U0001F64F", "\U0001F44B", "\U0001F389",
                "\U0001F525", "\u2705", "\U0001F4E1", "\U0001F4CD")


def emoji_order(available):
    """The picker's emoji: the common ones the system has first, then the rest as given.
    A trailing emoji variation selector does not count when matching."""
    bare = lambda e: e.replace("\ufe0f", "")
    out = []
    taken = set()
    for c in COMMON_EMOJI:
        for e in available:
            if bare(e) == c and e not in taken:
                out.append(e)
                taken.add(e)
                break
    return out + [e for e in available if e not in taken]
