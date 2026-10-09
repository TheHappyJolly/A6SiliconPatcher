"""Settings added by this port (not part of the original game): a small json file next to the game's save data."""
import json, os, threading
from . import foundation

_lock = threading.Lock()
_cache = None
DEFAULTS = {"wheels3d": False, "duel_original": True}


def _path():
    return os.path.join(foundation.HOME_SUPPORT, "a6settings.json")


def _load():
    global _cache
    if _cache is None:
        d = dict(DEFAULTS)
        try:
            d.update(json.load(open(_path())))
        except Exception:
            pass
        _cache = d
    return _cache


def get(key):
    with _lock:
        return _load()[key]


def set(key, value):
    with _lock:
        d = _load(); d[key] = value
        try:
            os.makedirs(os.path.dirname(_path()), exist_ok=True)
            tmp = _path() + ".tmp"
            json.dump(d, open(tmp, "w")); os.replace(tmp, _path())
        except Exception:
            pass
