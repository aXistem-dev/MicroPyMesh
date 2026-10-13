"""Radio tab: is the radio listening, how noisy is the channel, adverts, and the preset."""

import lvgl as lv

from mpos import Intent

import ui_model
import ui_theme as T
import setup_activity
from meshcore_presets import DEFAULT_POWER
from ui_tabs import Tab

REFRESH_MS = 5000
CHART_POINTS = 180
CHART_W = 424
CHART_H = 56
BASELINE_Y = 44          # dashed reference line, from the top of the chart
NOISE_RANGE = (-125, -60)


class RadioTab(Tab):
    title = "Radio"

    def build(self, parent, activity):
        self.activity = activity
        self.mgr = activity.mgr
        self._note_until = 0
        self._timer = None
        header = T.HeaderTop(parent, "Radio", pad_right=16)
        self.status = T.label(header.obj, "", 15, col=T.MUTED)
        body = T.scroll_area(parent, 14, 10)
        body.set_style_pad_bottom(10, lv.PART.MAIN)

        card = T.card(body, filled=True, pad_ver=12, pad_hor=14, gap=6)
        hero = T.row(card, lv.pct(100), lv.SIZE_CONTENT, 10, lv.FLEX_ALIGN.END)
        self.noise = T.label(hero, "—", 30, 500, mono=True)
        unit = T.label(hero, "dBm noise floor", 16, col=T.MUTED)
        unit.set_flex_grow(1)
        unit.set_style_pad_bottom(5, lv.PART.MAIN)
        T.label(hero, "last 30 min", 14, col=T.MUTED).set_style_pad_bottom(6, lv.PART.MAIN)
        self._build_chart(card)
        stats = T.row(card, lv.pct(100), 21, 0)
        self._stats = []
        for _ in range(3):
            cell = T.row(stats, 141, 21, 4)
            name = T.label(cell, "", 15, col=T.MUTED)
            value = T.label(cell, "", 15, mono=True)
            self._stats.append((name, value))

        buttons = T.row(body, lv.pct(100), 56, 10)
        zero = T.button(buttons, "Zero-hop advert", lambda: self.advert(False), "outline", 56,
                        "neighbours only", 1, 17)
        flood = T.button(buttons, "Flood advert", lambda: self.advert(True), "primary", 56,
                         "whole mesh", 1, 17)
        for b in (zero, flood):
            b.set_flex_grow(1)

        preset = T.row(body, lv.pct(100), lv.SIZE_CONTENT, 10)
        T.outline(preset, T.OUTLINE, 12)
        preset.set_style_pad_ver(10, lv.PART.MAIN)
        preset.set_style_pad_left(14, lv.PART.MAIN)
        preset.set_style_pad_right(6, lv.PART.MAIN)
        title, detail, air = ui_model.preset_summary(self.mgr.radio_preset(), DEFAULT_POWER)
        text = T.column(preset, 1, lv.SIZE_CONTENT, 2)
        text.set_flex_grow(1)
        T.label(text, title, 18, 600)
        T.label(text, detail, 12, mono=True, col=T.MUTED, long_mode=lv.label.LONG_MODE.WRAP,
                width=lv.pct(100))
        T.label(text, air, 14, col=T.MUTED)
        T.button(preset, "Change", self.change_preset, "filled", 44, size=16)

        heard = T.row(body, lv.pct(100), 24, 8)
        T.label(heard, "Recently heard", 15, col=T.MUTED).set_flex_grow(1)
        self.rate = T.label(heard, "", 13, mono=True, col=T.MUTED)
        self.heard = T.column(body, lv.pct(100), lv.SIZE_CONTENT, 2)

        self._timer = lv.timer_create(lambda t: self.refresh(), REFRESH_MS, None)
        self.refresh()

    def _build_chart(self, parent):
        holder = T.box(parent, CHART_W, CHART_H)
        self._dash_points = [{"x": 0, "y": BASELINE_Y}, {"x": CHART_W, "y": BASELINE_Y}]
        base = lv.line(holder)
        base.set_points(self._dash_points, 2)
        base.set_style_line_color(T.color(T.OUTLINE), lv.PART.MAIN)
        base.set_style_line_width(1, lv.PART.MAIN)
        base.set_style_line_dash_width(3, lv.PART.MAIN)
        base.set_style_line_dash_gap(4, lv.PART.MAIN)
        self.chart = lv.chart(holder)
        self.chart.set_size(CHART_W, CHART_H)
        self.chart.set_type(lv.chart.TYPE.LINE)
        self.chart.set_point_count(CHART_POINTS)
        self.chart.set_div_line_count(0, 0)
        self.chart.set_axis_range(lv.chart.AXIS.PRIMARY_Y, NOISE_RANGE[0], NOISE_RANGE[1])
        self.chart.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        self.chart.set_style_border_width(0, lv.PART.MAIN)
        self.chart.set_style_pad_all(0, lv.PART.MAIN)
        self.chart.set_style_size(0, 0, lv.PART.INDICATOR)
        self.chart.set_style_line_width(2, lv.PART.ITEMS)
        self.chart.set_style_line_rounded(True, lv.PART.ITEMS)
        self.series = self.chart.add_series(T.color(T.ACCENT), lv.chart.AXIS.PRIMARY_Y)

    def advert(self, flood):
        ok, err = self.mgr.advertise(flood=flood)
        if ok:
            self._note("Flood advert sent" if flood else "Zero-hop advert sent", T.MUTED)
        else:
            self._note(err or "Advert failed", T.FAIL_TEXT)

    def _note(self, text, col):
        """Show a result in the header until the next-but-one refresh."""
        self.status.set_text(text)
        self.status.set_style_text_color(T.color(col), lv.PART.MAIN)
        self._note_until = 2

    def change_preset(self):
        intent = Intent(activity_class=setup_activity.SetupActivity)
        intent.putExtra("step", 2)
        self.activity.startActivity(intent)

    def refresh(self):
        st = self.mgr.radio_stats()
        t = ui_model.radio_texts(st)
        if self._note_until:
            self._note_until -= 1
        else:
            self.status.set_text(t["subtitle"])
            self.status.set_style_text_color(T.color(T.MUTED), lv.PART.MAIN)
        self.noise.set_text(t["noise"])
        for (name, value), (n, v) in zip(self._stats, t["stats"]):
            name.set_text(n)
            value.set_text(v)
        self.rate.set_text(ui_model.rx_rate_text(st.get("rx_per_min", 0)))
        self._fill_heard(st.get("recent") or [])
        series = st.get("noise_series") or []
        self.chart.set_all_values(self.series, lv.CHART_POINT_NONE)
        for v in series[-CHART_POINTS:]:
            self.chart.set_next_value(self.series, int(v))
        self.chart.refresh()

    _COLS = (44, 56, 86, 52)     # age, kind, RSSI, SNR; hops takes the rest

    def _fill_heard(self, recent):
        self.heard.clean()
        rows = ui_model.recent_rows(recent[:8])
        if not rows:
            T.label(self.heard, "Nothing heard yet.", 15, col=T.MUTED)
            return
        for cells in rows:
            line = T.row(self.heard, lv.pct(100), 20, 0)
            for i, text in enumerate(cells):
                lb = T.label(line, text, 13, mono=True, col=T.ACCENT if i == 1 else T.TEXT)
                if i < len(self._COLS):
                    lb.set_width(self._COLS[i])

    def destroy(self):
        if self._timer is not None:
            self._timer.delete()
            self._timer = None
