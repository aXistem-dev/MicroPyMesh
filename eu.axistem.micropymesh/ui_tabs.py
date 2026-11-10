"""Base class of the main screen's tabs. Each tab is built when first shown and torn down
when another tab takes its place, so only one tab's widgets exist at a time."""


class Tab:
    title = ""

    def __init__(self):
        self.activity = None
        self.mgr = None

    def build(self, parent, activity):
        """Fill `parent` (480 x 424, above the tab bar)."""
        raise NotImplementedError

    def on_event(self, event, data):
        """A manager event, already on the LVGL thread."""

    def on_resume(self):
        """The main screen is back on top: catch up on what changed meanwhile (messages read
        in a thread, settings changed on a sub-page)."""
        self.on_event("unread", None)

    def destroy(self):
        """Called before the tab's widgets are deleted (stop timers here)."""
