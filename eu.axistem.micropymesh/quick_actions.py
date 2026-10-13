"""Long-press menus of the chat and node lists: what the MeshCore apps offer on a contact or a
channel (open, mark read, sounds, route, ping, map, history, remove)."""

from mpos import Intent

import map_model
import ui_model
import ui_theme as T

_SOUNDS = (("Follow the settings", "default"), ("Always beep", "on"), ("Never beep", "off"))


def confirm(title, text, action, callback):
    """Ask before something that cannot be undone: the action (in red) or Cancel."""
    return T.ActionSheet(title, [(action, callback, "danger"), ("Cancel", lambda: None)], text)


def ask_remove_contact(mgr, pk, name, then=None):
    """Remove a contact; with a chat, ask whether the chat goes too."""
    def go(delete_chat):
        mgr.remove_contact(pk, delete_chat=delete_chat)
        if then:
            then()
    if mgr.get_dm_messages(pk):
        return T.ActionSheet("Remove %s?" % name, [
            ("Remove contact, keep the chat", lambda: go(False), "danger"),
            ("Remove contact and chat", lambda: go(True), "danger"),
            ("Cancel", lambda: None)],
            "It stays among the discovered nodes. A kept chat can be read; to write again, "
            "add the contact again.")
    return confirm("Remove %s?" % name, "It stays among the discovered nodes.",
                   "Remove contact", lambda: go(False))


def ask_remove_chat(mgr, key, name, channel=False):
    if channel:
        return confirm("Remove %s?" % name, "You leave the channel and its messages go; "
                       "join again with its name or key.", "Remove channel",
                       lambda: mgr.remove_chat(key))
    return confirm("Remove the chat with %s?" % name, "Its messages go; the contact stays.",
                   "Remove chat", lambda: mgr.remove_chat(key))


def ask_forget(mgr, pk, name, then=None):
    def go():
        mgr.forget_node(pk)
        if then:
            then()
    return confirm("Remove %s from discovered?" % name, "It comes back when it is heard "
                   "again.", "Remove from discovered", go)


def _open(activity, cls, **extras):
    intent = Intent(activity_class=cls)
    for k, v in extras.items():
        intent.putExtra(k, v)
    activity.startActivity(intent)


def sounds_menu(mgr, key, title):
    cur = mgr.sound_override(key)
    return T.ActionSheet(title, [(text, lambda v=value: mgr.set_sound_override(key, v),
                                  "checked" if value == cur else None)
                                 for text, value in _SOUNDS], "Sounds")


def chat_menu(activity, mgr, key, kind, open_chat):
    """A chat in the Chats list: a channel (key = its name) or a contact (key = pubkey)."""
    import routing_pages
    import thread_activity
    if kind == "channel":
        title = key
    else:
        title = ui_model.display(mgr.chat_name(key)) or key[:8]
    actions = [("Open", lambda: open_chat(key, kind))]
    if mgr.get_unread(key):
        actions.append(("Mark as read", lambda: mgr.clear_unread(key)))
    actions.append(("Sounds…", lambda: sounds_menu(mgr, key, title)))
    if kind == "channel":
        actions.append(("Region scope…",
                        lambda: _open(activity, routing_pages.ChannelScopeActivity, channel=key)))
        actions.append(("Channel info",
                        lambda: _open(activity, thread_activity.ChannelInfoActivity, channel=key)))
        actions.append(("Clear history", lambda: confirm(
            "Clear %s?" % title, "Every message in this channel goes.", "Clear history",
            lambda: mgr.clear_history(key)), "danger"))
        if key != "Public":
            actions.append(("Remove channel", lambda: ask_remove_chat(mgr, key, title, True),
                            "danger"))
    else:
        actions += node_actions(activity, mgr, key, chat=False)
        actions.append(("Clear history", lambda: confirm(
            "Clear the chat with %s?" % title, "Every message in this chat goes.",
            "Clear history", lambda: mgr.clear_history(key)), "danger"))
        actions.append(("Remove chat", lambda: ask_remove_chat(mgr, key, title), "danger"))
        if mgr.is_contact(key):
            actions.append(("Remove contact", lambda: ask_remove_contact(mgr, key, title),
                            "danger"))
    return T.ActionSheet(title, actions, "Channel" if kind == "channel" else "Contact")


def node_actions(activity, mgr, pk, chat=True):
    """Actions on a node or contact: chat or details, route, ping, map."""
    import map_view
    import node_activity
    import routing_pages
    import thread_activity
    node = mgr.get_node(pk) or mgr.get_contact(pk) or {}
    kind = {1: "chat", 2: "rptr", 3: "room", 4: "sensor"}.get(node.get("type"), "chat")
    out = []
    if chat and kind == "chat":
        out.append(("Open chat", lambda: map_view.open_node(activity, mgr, pk, "chat")))
    if kind != "chat":
        out.append(("Details", lambda: _open(activity, node_activity.NodeDetailActivity,
                                              pubkey=pk)))
    if mgr.is_contact(pk) and kind in ("chat", "room"):
        out.append(("Route…", lambda: _open(activity, routing_pages.RoutingActivity,
                                                pubkey=pk)))
    out.append(("Ping", lambda: mgr.ping(pk)))
    if map_model.positions([node]):
        out.append(("Show on map", lambda: _open(activity, map_view.MapActivity, pubkey=pk)))
    return out


def node_menu(activity, mgr, pk, discovered=False):
    """A node in the contacts or the discovered list."""
    node = mgr.get_node(pk) or mgr.get_contact(pk) or {}
    title = ui_model.display(node.get("name")) or pk[:8]
    actions = node_actions(activity, mgr, pk)
    if mgr.is_contact(pk):
        fav = mgr.is_favourite(pk)
        actions.append(("Remove from favourites" if fav else "Add to favourites",
                        lambda: mgr.set_favourite(pk, not fav)))
        actions.append(("Sounds…", lambda: sounds_menu(mgr, pk, title)))
        if mgr.is_blocked_key(pk):
            actions.append(("Unblock", lambda: mgr.unblock_key(pk)))
        else:
            actions.append(("Block", lambda: mgr.block_key(pk, title), "danger"))
        actions.append(("Remove contact", lambda: ask_remove_contact(mgr, pk, title), "danger"))
    else:
        actions.append(("Add to contacts", lambda: mgr.add_contact(pk, node.get("name"),
                                                                    node.get("type", 1))))
    if discovered:
        actions.append(("Remove from discovered", lambda: ask_forget(mgr, pk, title), "danger"))
    return T.ActionSheet(title, actions, "%s · %s" % (
        {1: "companion", 2: "repeater", 3: "room server", 4: "sensor"}.get(node.get("type"),
                                                                          "node"), pk[:8].upper()))
