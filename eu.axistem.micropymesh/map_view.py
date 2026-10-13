"""The map: offline tiles from the SD card (/sdcard/maps/<style>, see map_tiles) with a pin for
every node that sent its position and a marker for our own. Drag to pan, + and - to zoom, the
fit button shows all pins, the "me" button centres on us; a tap on a pin opens that node. Used
by the Map tab, by MapActivity (a node's Map button, centred on that node) and by
MapPickActivity (choose our position under a crosshair)."""

import lvgl as lv

from mpos import Activity, Intent

import map_model as M
import map_tiles
import ui_model
import ui_theme as T
from meshcore_manager import MeshCoreManager

MAPS = None                     # the maps folder; None: "maps" on the SD card (sd_root)


def maps_dir():
    """Where the map styles live: one folder per style, <style>/{z}/{x}/{y}.png."""
    if MAPS:
        return MAPS
    from meshcore_manager import sd_root
    return sd_root().rstrip("/") + "/maps"
LAND = {"dark": 0x141B23, "light": 0xEEF1F4}   # land colour of our styles, shown while loading
HOME = (50.64, 4.67, 8)         # Belgium, when no node has a position
CREDIT = "© OpenMapTiles © OpenStreetMap contributors"
PIN = 14
ME = 18                         # our own marker: a light dot in an accent ring
SPOT = 26                       # a place from a message: a dark ring in the accent colour
ME_ZOOM = 15
LABEL_ZOOM = 13                 # names next to the pins from this zoom on
TAP_PX = 28
DRAG_PX = 8


def pin_color(kind):
    return {"chat": T.ACCENT, "rptr": T.PIN_RPTR, "room": T.PIN_ROOM}.get(kind, T.MUTED)


def map_styles():
    """The style folders on the card, sorted (empty without a card)."""
    import os
    try:
        names = []
        for entry in os.ilistdir(maps_dir()):
            if entry[1] == 0x4000 and not entry[0].startswith("."):
                names.append(entry[0])
        return sorted(names)
    except OSError:
        return []


def chosen_style():
    """The Map setting: "system" (follow the app's theme) or a style folder's name."""
    from mpos import SharedPreferences
    from meshcore_manager import MESHCORE_APP
    return SharedPreferences(MESHCORE_APP).get_string("map_style", "system") or "system"


def tile_style(choice=None, available=None):
    """The style folder to draw: the chosen one, or for "system" the theme's (light or dark).
    One that is not on the card gives way to the other of light/dark, then to any style."""
    choice = chosen_style() if choice is None else choice
    available = map_styles() if available is None else available
    theme = "light" if (T.palette or {}).get("LIGHT") else "dark"
    wanted = theme if choice == "system" else choice
    for name in (wanted, theme, "dark" if theme == "light" else "light"):
        if name in available:
            return name
    return available[0] if available else wanted


def _credit(style):
    """The first line of the style folder's credit.txt, else the OpenStreetMap credit."""
    try:
        with open("%s/%s/credit.txt" % (maps_dir(), style)) as f:
            line = f.readline().strip()
        return line or CREDIT
    except OSError:
        return CREDIT


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


def _nodes(mgr, source="all"):
    """The nodes to pin: "contacts" (with what was last heard of them), or "all": every node
    heard and every contact, each once."""
    if source == "contacts":
        return mgr.get_contact_nodes()
    out = list(mgr.get_learned_companions())
    seen = set(n.get("pubkey") for n in out)
    for c in mgr.get_contacts():
        if c.get("pubkey") not in seen:
            out.append(c)
    return out


class _Slot:
    """One tile image on screen."""

    def __init__(self, parent):
        self.img = lv.image(parent)
        self.img.remove_flag(lv.obj.FLAG.CLICKABLE)
        self.key = None
        self.entry = None
        self.dsc = None

    def show(self, key, sx, sy, entry):
        self.img.set_pos(sx, sy)
        self.key = key
        if entry is self.entry:
            return
        self.entry = entry
        if entry is None or entry[0] == "none":
            self.img.add_flag(lv.obj.FLAG.HIDDEN)
            return
        if entry[0] == "rgb565":
            dsc = lv.image_dsc_t({"header": {"magic": lv.IMAGE_HEADER_MAGIC,
                                             "cf": lv.COLOR_FORMAT.RGB565,
                                             "w": M.TILE, "h": M.TILE, "stride": 2 * M.TILE},
                                  "data_size": map_tiles.TILE_BYTES, "data": entry[1]})
        else:   # a PNG only LVGL can read
            dsc = lv.image_dsc_t({"header": {"magic": lv.IMAGE_HEADER_MAGIC,
                                             "cf": lv.COLOR_FORMAT.RAW_ALPHA,
                                             "w": M.TILE, "h": M.TILE},
                                  "data_size": len(entry[1]), "data": entry[1]})
        self.img.set_src(dsc)
        self.dsc = dsc                  # keeps the descriptor alive while LVGL draws it
        self.img.remove_flag(lv.obj.FLAG.HIDDEN)

    def hide(self):
        self.key = None
        self.entry = None
        self.img.add_flag(lv.obj.FLAG.HIDDEN)


class MapView:

    def __init__(self, parent, mgr, w, h, open_node, focus=None, controls=True,
                 source="contacts", spot=None):
        self.mgr = mgr
        self.source = source
        self.w, self.h = w, h
        self.open_node = open_node
        self.style = tile_style()
        self.obj = T.box(parent, w, h)
        T.fill(self.obj, LAND.get(self.style, T.BG))
        self.obj.add_flag(lv.obj.FLAG.CLICKABLE)
        T.on(self.obj, lv.EVENT.PRESSED, self._on_press)
        T.on(self.obj, lv.EVENT.PRESSING, self._on_pressing)
        T.on(self.obj, lv.EVENT.RELEASED, self._on_release)
        T.on(self.obj, lv.EVENT.PRESS_LOST, self._on_release)
        self.tiles = T.box(self.obj, w, h)
        self.tiles.set_pos(0, 0)
        self.pin_layer = T.box(self.obj, w, h)
        self.pin_layer.set_pos(0, 0)
        self._slots = []
        self._pins = {}             # pubkey -> (dot, label)
        self.me = T.box(self.pin_layer, ME, ME)
        T.fill(self.me, T.TEXT, ME // 2, T.ACCENT, 4)
        self.me.add_flag(lv.obj.FLAG.HIDDEN)
        self.spot_at = spot           # (lat, lon) of a place to mark, e.g. from a message
        self.spot = T.box(self.pin_layer, SPOT, SPOT)
        T.fill(self.spot, T.BG, SPOT // 2, T.ACCENT, 4)
        self.spot.add_flag(lv.obj.FLAG.HIDDEN)
        self._pin_model = []
        self._drag = None
        self._ready = False
        self.note = T.label(self.obj, "", 15, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                            width=w - 96)
        self.note.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        T.fill(self.note, T.BG, 10)
        self.note.set_style_bg_opa(lv.OPA._80, lv.PART.MAIN)
        self.note.set_style_pad_all(10, lv.PART.MAIN)
        self.note.align(lv.ALIGN.CENTER, 0, 0)
        self.note.add_flag(lv.obj.FLAG.HIDDEN)
        self._me_button = None
        if controls:
            self._controls()
        credit = T.label(self.obj, _credit(self.style), 11, col=T.MUTED)
        self.credit = credit
        T.fill(credit, T.BG, 6)
        credit.set_style_bg_opa(lv.OPA._70, lv.PART.MAIN)
        credit.set_style_pad_hor(6, lv.PART.MAIN)
        credit.set_style_pad_ver(2, lv.PART.MAIN)
        credit.align(lv.ALIGN.BOTTOM_LEFT, 4, -4)
        self.store = map_tiles.TileStore(maps_dir() + "/" + self.style, _read_file, capacity=20,
                                         on_ready=self._on_tile)
        self.store.start()
        self._timer = lv.timer_create(lambda t: self._tick(), 40, None)
        self._set_view(*self._initial_view(focus))

    def destroy(self):
        self.store.stop()
        if self._timer is not None:
            self._timer.delete()
            self._timer = None

    # --- view ----------------------------------------------------------------- #
    def _controls(self):
        col = T.column(self.obj, 48, lv.SIZE_CONTENT, 8)
        col.align(lv.ALIGN.TOP_RIGHT, -8, 8)
        for name, cb in (("plus", lambda: self.zoom(1)), ("minus", lambda: self.zoom(-1)),
                         ("fit", self.fit_all), ("me", self.center_me)):
            b = T.icon_button(col, name, cb)
            T.fill(b, T.SURFACE, 10, T.OUTLINE)
        self._me_button = b

    def _initial_view(self, focus):
        nodes = _nodes(self.mgr, self.source)
        me = self.mgr.position()
        if focus == "me" and me:
            return me["lat"], me["lon"], ME_ZOOM
        if isinstance(focus, tuple):
            return focus[0], focus[1], 15
        if focus is not None:
            for n in nodes:
                if n.get("pubkey") == focus and M.positions([n]):
                    return n["lat"], n["lon"], 15
        view = M.fit(self._points(nodes), self.w, self.h)
        return view if view is not None else HOME

    def _points(self, nodes):
        pts = M.positions(nodes)
        me = self.mgr.position()
        if me:
            pts.append((me["lat"], me["lon"]))
        return pts

    def center(self):
        """(lat, lon) under the middle of the view."""
        return M.latlon(self.cx, self.cy, self.z)

    def center_me(self):
        me = self.mgr.position()
        if me:
            self._set_view(me["lat"], me["lon"], max(self.z, ME_ZOOM))

    def _set_view(self, lat, lon, z):
        self.z = M.clamp_zoom(z)
        self.cx, self.cy = M.world_px(lat, lon, self.z)
        self.refresh_pins()
        self.layout()

    def zoom(self, dz):
        z = M.clamp_zoom(self.z + dz)
        if z == self.z:
            return
        f = 2.0 ** (z - self.z)
        self.cx, self.cy, self.z = self.cx * f, self.cy * f, z
        self.refresh_pins()
        self.layout()

    def fit_all(self):
        view = M.fit(self._points(_nodes(self.mgr, self.source)), self.w, self.h)
        self._set_view(*(view or HOME))

    def layout(self, load=True):
        """Place the tiles and pins for the current view; `load` asks for the missing tiles
        (not while dragging: decoding then would make the drag stutter)."""
        world = M.TILE * (1 << self.z)
        self.cy = max(0, min(world, self.cy))
        tiles = M.visible_tiles(self.cx, self.cy, self.z, self.w, self.h)
        if load:
            mid_x, mid_y = self.w / 2 - M.TILE / 2, self.h / 2 - M.TILE / 2
            order = sorted(tiles, key=lambda t: (t[3] - mid_x) ** 2 + (t[4] - mid_y) ** 2)
            self.store.want([t[:3] for t in order])
        while len(self._slots) < len(tiles):
            self._slots.append(_Slot(self.tiles))
        for slot, (z, x, y, sx, sy) in zip(self._slots, tiles):
            slot.show((z, x, y), sx, sy, self.store.get((z, x, y)))
        for slot in self._slots[len(tiles):]:
            slot.hide()
        self._place_pins()
        self._update_note()

    def _on_tile(self, key):
        self._ready = True          # loader thread: the LVGL timer picks it up

    def _tick(self):
        if not self._ready:
            if self.store.status == "nocard":
                self._update_note()
            return
        self._ready = False
        for slot in self._slots:
            if slot.key is not None:
                entry = self.store.get(slot.key)
                if entry is not slot.entry:
                    slot.show(slot.key, slot.img.get_x(), slot.img.get_y(), entry)
        self._update_note()

    def _update_note(self):
        if self.store.status == "nocard":
            text = "No SD card. The map's tiles are read from the card."
        elif all(s.entry is not None and s.entry[0] == "none" for s in self._slots if s.key):
            text = "No map tiles here. Copy maps/%s to the SD card." % self.style
        else:
            text = ""
        if text:
            self.note.set_text(text)
            self.note.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.note.add_flag(lv.obj.FLAG.HIDDEN)

    # --- pins ----------------------------------------------------------------- #
    def refresh_pins(self):
        self._nodes = _nodes(self.mgr, self.source)
        self._place_pins()

    def _place_me(self):
        me = self.mgr.position()
        if self._me_button is not None:
            if me:
                self._me_button.remove_flag(lv.obj.FLAG.HIDDEN)
            else:
                self._me_button.add_flag(lv.obj.FLAG.HIDDEN)
        if not me:
            self.me.add_flag(lv.obj.FLAG.HIDDEN)
            return
        x, y = M.world_px(me["lat"], me["lon"], self.z)
        sx, sy = x - (self.cx - self.w / 2), y - (self.cy - self.h / 2)
        self.me_xy = (sx, sy)
        if -ME <= sx <= self.w + ME and -ME <= sy <= self.h + ME:
            self.me.set_pos(int(sx) - ME // 2, int(sy) - ME // 2)
            self.me.remove_flag(lv.obj.FLAG.HIDDEN)
            self.me.move_foreground()
        else:
            self.me.add_flag(lv.obj.FLAG.HIDDEN)

    def _place_spot(self):
        if self.spot_at is None:
            return
        x, y = M.world_px(self.spot_at[0], self.spot_at[1], self.z)
        sx, sy = x - (self.cx - self.w / 2), y - (self.cy - self.h / 2)
        if -SPOT <= sx <= self.w + SPOT and -SPOT <= sy <= self.h + SPOT:
            self.spot.set_pos(int(sx) - SPOT // 2, int(sy) - SPOT // 2)
            self.spot.remove_flag(lv.obj.FLAG.HIDDEN)
            self.spot.move_foreground()
        else:
            self.spot.add_flag(lv.obj.FLAG.HIDDEN)

    def _place_pins(self):
        self._place_me()
        self._place_spot()
        model = M.pins(self._nodes, self.cx, self.cy, self.z, self.w, self.h)
        contacts = set(c["pubkey"] for c in self.mgr.get_contacts())
        for p in model:
            p["contact"] = p["pubkey"] in contacts
        self._pin_model = model
        names = self.z >= LABEL_ZOOM or len(model) <= 12
        keep = set()
        for p in model:
            pk = p["pubkey"]
            keep.add(pk)
            dot, name = self._pins.get(pk) or self._new_pin(pk)
            if p["contact"]:
                T.fill(dot, pin_color(p["kind"]), PIN // 2, T.BG, 2)
            else:                       # heard, not added: a ring
                T.fill(dot, T.BG, PIN // 2, pin_color(p["kind"]), 3)
            dot.set_pos(int(p["sx"]) - PIN // 2, int(p["sy"]) - PIN // 2)
            dot.remove_flag(lv.obj.FLAG.HIDDEN)
            if names:
                name.set_text(ui_model.display(p["name"]) or p["name"])
                name.set_pos(int(p["sx"]) + PIN // 2 + 3, int(p["sy"]) - 9)
                name.remove_flag(lv.obj.FLAG.HIDDEN)
            else:
                name.add_flag(lv.obj.FLAG.HIDDEN)
        for pk, (dot, name) in self._pins.items():
            if pk not in keep:
                dot.add_flag(lv.obj.FLAG.HIDDEN)
                name.add_flag(lv.obj.FLAG.HIDDEN)

    def _new_pin(self, pk):
        dot = T.box(self.pin_layer, PIN, PIN)
        name = T.label(self.pin_layer, "", 13, 600, col=T.TEXT)
        T.fill(name, T.BG, 4)
        name.set_style_bg_opa(lv.OPA._60, lv.PART.MAIN)
        name.set_style_pad_hor(3, lv.PART.MAIN)
        self._pins[pk] = (dot, name)
        return dot, name

    # --- touch ---------------------------------------------------------------- #
    def _point(self):
        p = lv.point_t()
        lv.indev_active().get_point(p)
        a = lv.area_t()
        self.obj.get_coords(a)
        return p.x - a.x1, p.y - a.y1

    def _on_press(self, e):
        x, y = self._point()
        self._drag = [x, y, self.cx, self.cy, False]
        self.store.pause(True)

    def _on_pressing(self, e):
        d = self._drag
        if d is None:
            return
        x, y = self._point()
        dx, dy = x - d[0], y - d[1]
        if not d[4] and abs(dx) + abs(dy) < DRAG_PX:
            return
        d[4] = True
        self.cx, self.cy = d[2] - dx, d[3] - dy
        self.layout(load=False)

    def _on_release(self, e):
        d = self._drag
        self._drag = None
        self.store.pause(False)
        if d is not None and d[4]:
            self.layout()
        if d is None or d[4]:
            return
        best = self.pin_at(*self._point())
        if best is not None:
            self.open_node(best["pubkey"], best["kind"])

    def pin_at(self, x, y):
        """The pin nearest to (x, y) in the map, within TAP_PX, or None."""
        best, best_d = None, TAP_PX * TAP_PX
        for p in self._pin_model:
            dd = (p["sx"] - x) ** 2 + (p["sy"] - y) ** 2
            if dd <= best_d:
                best, best_d = p, dd
        return best


def open_node(activity, mgr, pubkey, kind):
    """A pin's node: companions open their chat, everything else its detail screen."""
    import node_activity
    import thread_activity
    if kind == "chat":
        if not mgr.is_contact(pubkey):
            mgr.add_contact(pubkey)
        intent = Intent(activity_class=thread_activity.DMChatActivity)
    else:
        intent = Intent(activity_class=node_activity.NodeDetailActivity)
    intent.putExtra("pubkey", pubkey)
    activity.startActivity(intent)


class DiscoveredMapActivity(Activity):
    """Every node heard on a map: contacts as dots, nodes not added as rings. A tap on a pin
    opens the node's menu (add it, details, ping...)."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        scr = T.make_screen()
        T.HeaderSub(scr, "Discovered nodes", "Map", back=self.finish)
        self.view = MapView(scr, self.mgr, T.W, T.H - T.TOP - T.HEADER_H, self.menu,
                            source="all")
        self.setContentView(scr)

    def menu(self, pubkey, kind):
        import quick_actions
        self.sheet = quick_actions.node_menu(self, self.mgr, pubkey, discovered=True)

    def onPause(self, screen):
        T.close_sheets()
        super().onPause(screen)

    def onDestroy(self, screen):
        self.view.destroy()


class MapActivity(Activity):
    """The map on its own screen, centred on the node in extras["pubkey"], or on the place
    in extras["lat"], extras["lon"] (marked with a ring)."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        extras = self.getIntent().extras or {}
        pk = extras.get("pubkey")
        scr = T.make_screen()
        spot = None
        if extras.get("lat") is not None and extras.get("lon") is not None:
            spot = (extras["lat"], extras["lon"])
            T.HeaderSub(scr, "Shared place", "%.4f, %.4f" % spot, back=self.finish)
        else:
            node = self.mgr.get_node(pk) or self.mgr.get_contact(pk) or {}
            T.HeaderSub(scr, ui_model.display(node.get("name")) or "Map", "Map", back=self.finish)
        self.view = MapView(scr, self.mgr, T.W, T.H - T.TOP - T.HEADER_H,
                            lambda p, k: open_node(self, self.mgr, p, k),
                            focus=spot if spot else pk, source="all", spot=spot)
        self.setContentView(scr)

    def onDestroy(self, screen):
        self.view.destroy()


class MapPickActivity(Activity):
    """Choose our position: move the map until the crosshair is on the spot, then use it."""

    def onCreate(self):
        self.mgr = MeshCoreManager.get_instance()
        scr = T.make_screen()
        T.HeaderSub(scr, "Pick your position", back=self.finish)
        bar_h = 72
        self.view = MapView(scr, self.mgr, T.W, T.H - T.TOP - T.HEADER_H - bar_h,
                            lambda p, k: None, focus="me", controls=True)
        cross = T.box(self.view.obj, 40, 40)
        T.outline(cross, T.ACCENT, 20, 3)
        cross.align(lv.ALIGN.CENTER, 0, 0)
        dot = T.box(cross, 6, 6)
        T.fill(dot, T.ACCENT, 3)
        dot.center()
        self.crosshair = cross
        bar = T.row(scr, T.W, bar_h, 8)
        bar.set_style_pad_hor(14, lv.PART.MAIN)
        self.button = T.button(bar, "Use this spot", self.use, width=lv.pct(100))
        self.setContentView(scr)

    def use(self):
        lat, lon = self.view.center()
        ok, err = self.mgr.set_position(lat, lon)
        if ok:
            self.finish()

    def onDestroy(self, screen):
        self.view.destroy()
