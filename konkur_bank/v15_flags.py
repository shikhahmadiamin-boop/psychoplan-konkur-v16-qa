"""Feature gates without editing Django settings or building Next.js.

Markers are installed by an operator with ownership/mode checks. Disabling either
flag is an immediate read-time operation and does not alter stored exam attempts.
"""
from pathlib import Path
from django.conf import settings
_BASE = Path(__file__).resolve().parent

def _marker(filename):
    p = _BASE / filename
    return p.is_file() and not p.is_symlink()

def portal_enabled():
    return bool(getattr(settings, 'PP_KONKUR_V15_ENABLED', False)) or _marker('.pp_v15_portal_enabled')

def publish_enabled():
    return portal_enabled() and (bool(getattr(settings, 'PP_KONKUR_V15_PUBLISH_ENABLED', False))
                                 or _marker('.pp_v15_publish_enabled'))
