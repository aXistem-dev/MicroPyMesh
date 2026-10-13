"""Map tab: the offline map with every node that sent its position (see map_view)."""

import lvgl as lv

import map_view
from ui_tabs import Tab


class MapTab(Tab):
    title = "Map"

    def build(self, parent, activity):
        self.activity = activity
        self.mgr = activity.mgr
        lv.obj.update_layout(parent)
        self.view = map_view.MapView(parent, self.mgr, parent.get_width(), parent.get_height(),
                                     lambda pk, kind: map_view.open_node(activity, self.mgr, pk, kind))

    def on_event(self, event, data):
        if event in ("node", "contacts", "position"):
            self.view.refresh_pins()

    def on_resume(self):
        self.view.refresh_pins()

    def destroy(self):
        self.view.destroy()
