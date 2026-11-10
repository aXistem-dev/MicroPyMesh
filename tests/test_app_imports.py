"""How the app's modules load on MicroPythonOS.

The OS runs an entry point (main_activity.py, meshcore_boot_service.py) as a script and takes
the app folder off sys.path once it is running. So an entry point can never be imported, and a
module imported inside a function only resolves if the entry point loaded it at start.

Run:  PYTHONPATH=eu.axistem.micropymesh python3 tests/test_app_imports.py
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import fake_mpos  # noqa: E402

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "eu.axistem.micropymesh")
ENTRY_POINTS = ("main_activity", "meshcore_boot_service")
MODULES = {f[:-3] for f in os.listdir(APP_DIR) if f.endswith(".py")}


def _assert(c, m=""):
    if not c:
        raise AssertionError(m)


def _tree(mod):
    with open(os.path.join(APP_DIR, mod + ".py")) as f:
        return ast.parse(f.read())


def _names(node):
    if isinstance(node, ast.Import):
        return {a.name for a in node.names}
    if isinstance(node, ast.ImportFrom) and node.module:
        return {node.module}
    return set()


def _top_imports(mod):
    out = set()
    for n in _tree(mod).body:
        nodes = ast.walk(n) if isinstance(n, (ast.Try, ast.If)) else [n]
        for x in nodes:
            out |= _names(x)
    return out & MODULES


def _lazy_imports(mod):
    out = set()
    for f in ast.walk(_tree(mod)):
        if isinstance(f, ast.FunctionDef):
            for x in ast.walk(f):
                out |= _names(x)
    return out & MODULES


def _loaded_by(entry):
    seen, todo = set(), [entry]
    while todo:
        m = todo.pop()
        if m not in seen:
            seen.add(m)
            todo += _top_imports(m)
    return seen - {entry}


def test_no_module_imports_an_entry_point():
    bad = sorted((m, e) for m in MODULES for e in ENTRY_POINTS
                 if e in (_top_imports(m) | _lazy_imports(m)))
    _assert(not bad, bad)


def test_lazy_imports_are_loaded_by_the_main_screen():
    loaded = _loaded_by("main_activity")
    bad = sorted((m, l) for m in loaded for l in _lazy_imports(m)
                 if l not in loaded and l not in ENTRY_POINTS)
    _assert(not bad, bad)



def test_lazy_imports_are_loaded_by_the_background_service():
    # With "Receive in background" on, the service runs the engine without any screen.
    loaded = _loaded_by("meshcore_boot_service")
    # A notification opens its chat only once the screens are loaded (it falls back to none).
    known = {("meshcore_manager", "thread_activity")}
    bad = sorted((m, l) for m in loaded for l in _lazy_imports(m)
                 if l not in loaded and l not in ENTRY_POINTS and (m, l) not in known)
    _assert(not bad, bad)


def test_event_callbacks_go_through_the_guard():
    # A callback that raises is never called again by this LVGL binding; T.on keeps it alive.
    bad = []
    for m in sorted(MODULES):
        with open(os.path.join(APP_DIR, m + ".py")) as f:
            for i, line in enumerate(f, 1):
                if "add_event_cb(" in line and not (m == "ui_theme" and "_guarded" in line):
                    bad.append("%s.py:%d" % (m, i))
    _assert(not bad, bad)


if __name__ == "__main__":
    fake_mpos.run_all(globals())
