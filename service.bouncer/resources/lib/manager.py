import os

try:
    import xbmc
    import xbmcaddon
    import xbmcgui
    import xbmcvfs
except ImportError:  # pragma: no cover - only used inside Kodi
    xbmc = None
    xbmcaddon = None
    xbmcgui = None
    xbmcvfs = None

from .whitelist import SCOPE_LABELS, grouped_entries, WhitelistStore


def _profile_store(addon):
    profile = xbmcvfs.translatePath(addon.getAddonInfo('profile'))
    os.makedirs(profile, exist_ok=True)
    return WhitelistStore.from_profile(profile)


def _notify(message, icon):
    xbmcgui.Dialog().notification('Bouncer', message, icon, 2500)


def manage_whitelist():
    addon = xbmcaddon.Addon()
    store = _profile_store(addon)

    while True:
        entries = store.list_entries()
        if not entries:
            _notify('Whitelist is empty', xbmcgui.NOTIFICATION_INFO)
            return

        grouped = grouped_entries(entries)
        scopes = [scope for scope, items in grouped.items() if items]
        labels = [
            '{} ({})'.format(SCOPE_LABELS[scope], len(grouped[scope]))
            for scope in scopes
        ]
        scope_index = xbmcgui.Dialog().select('Bouncer - Manage Whitelist', labels)
        if scope_index < 0:
            return

        scope = scopes[scope_index]
        scope_entries = grouped[scope]
        item_index = xbmcgui.Dialog().select(
            'Remove from {}'.format(SCOPE_LABELS[scope]),
            [entry['label'] for entry in scope_entries],
        )
        if item_index < 0:
            continue

        entry = scope_entries[item_index]
        confirmed = xbmcgui.Dialog().yesno(
            'Bouncer - Remove Whitelist',
            'Remove this whitelist entry?',
            '',
            entry['label'],
        )
        if not confirmed:
            continue

        store.remove_key(entry['key'])
        _notify('Removed whitelist entry', xbmcgui.NOTIFICATION_INFO)


def clear_whitelist():
    addon = xbmcaddon.Addon()
    store = _profile_store(addon)
    confirmed = xbmcgui.Dialog().yesno(
        'Bouncer - Clear Whitelist',
        'Remove every whitelist entry?',
    )
    if not confirmed:
        return

    store.clear()
    _notify('Whitelist cleared', xbmcgui.NOTIFICATION_INFO)


def run(action):
    if xbmc is None:  # pragma: no cover - only used inside Kodi
        raise RuntimeError('Kodi runtime unavailable')
    if action == 'manage_whitelist':
        manage_whitelist()
        return
    if action == 'clear_whitelist':
        clear_whitelist()
        return
    xbmcgui.Dialog().ok('Bouncer', 'Unknown action: {}'.format(action or '(none)'))
