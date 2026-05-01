"""
Bouncer - Rating-based playback control for Kodi
Addon ID: service.bouncer
Author:   dotJustin
Version:  1.1.0

Intercepts video playback and requires a PIN to continue if the content's
rating is in the user's blocked list. Supports one-time allows, timed session
unlocks, and hierarchical whitelisting for movies and TV content.
"""

import os
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.playback import extract_playback_context
from resources.lib.rating import is_rating_blocked, resolve_rating
from resources.lib.whitelist import (
    WhitelistStore,
    build_entry,
    menu_scopes_for_context,
    runtime_allow_key,
)


_TMDB_CACHE_MAX = 200


def log(msg, level=xbmc.LOGDEBUG):
    addon = xbmcaddon.Addon()
    if level >= xbmc.LOGINFO or addon.getSettingBool('debug_log'):
        xbmc.log('[Bouncer] {}'.format(msg), level)


class PinDialog(xbmcgui.WindowDialog):
    _BTN_1 = 100
    _BTN_2 = 101
    _BTN_3 = 102
    _BTN_4 = 103
    _BTN_5 = 104
    _BTN_6 = 105
    _BTN_7 = 106
    _BTN_8 = 107
    _BTN_9 = 108
    _BTN_DEL = 109
    _BTN_0 = 110
    _BTN_OK = 111

    _ACTION_BACK = 10
    _ACTION_BACK2 = 92

    _DIGITS = {
        _BTN_1: '1',
        _BTN_2: '2',
        _BTN_3: '3',
        _BTN_4: '4',
        _BTN_5: '5',
        _BTN_6: '6',
        _BTN_7: '7',
        _BTN_8: '8',
        _BTN_9: '9',
        _BTN_0: '0',
    }

    def __init__(self, heading, reason):
        super(PinDialog, self).__init__()
        self._heading = heading
        self._reason = reason
        self._pin = ''
        self._confirmed = False
        self._pin_label = None
        self._buttons = {}
        self._build()

    def _build(self):
        sw, sh = 1280, 720
        dw, dh = 660, 530
        dx = (sw - dw) // 2
        dy = (sh - dh) // 2

        self.addControl(xbmcgui.ControlImage(0, 0, sw, sh, '', colorDiffuse='BB000000'))
        self.addControl(xbmcgui.ControlImage(dx, dy, dw, dh, '', colorDiffuse='F0121220'))
        self.addControl(xbmcgui.ControlLabel(
            dx + 20, dy + 22, dw - 40, 48,
            self._heading, font='font14', textColor='FFFFFFFF', alignment=6
        ))
        self.addControl(xbmcgui.ControlLabel(
            dx + 20, dy + 74, dw - 40, 38,
            self._reason, font='font13', textColor='FF9999BB', alignment=6
        ))
        self.addControl(xbmcgui.ControlImage(
            dx + 30, dy + 118, dw - 60, 2, '', colorDiffuse='55FFFFFF'
        ))

        self._pin_label = xbmcgui.ControlLabel(
            dx + 20, dy + 130, dw - 40, 60,
            '- - - -', font='font30', textColor='FFFFFFFF', alignment=6
        )
        self.addControl(self._pin_label)

        btn_w, btn_h = 140, 65
        gap_x, gap_y = 18, 10
        grid_w = 3 * btn_w + 2 * gap_x
        bx = dx + (dw - grid_w) // 2
        by = dy + 215

        pad_layout = [
            (self._BTN_1, '1', 0, 0),
            (self._BTN_2, '2', 1, 0),
            (self._BTN_3, '3', 2, 0),
            (self._BTN_4, '4', 0, 1),
            (self._BTN_5, '5', 1, 1),
            (self._BTN_6, '6', 2, 1),
            (self._BTN_7, '7', 0, 2),
            (self._BTN_8, '8', 1, 2),
            (self._BTN_9, '9', 2, 2),
            (self._BTN_DEL, '\u232b', 0, 3),
            (self._BTN_0, '0', 1, 3),
            (self._BTN_OK, 'OK', 2, 3),
        ]

        for button_id, label, col, row in pad_layout:
            x = bx + col * (btn_w + gap_x)
            y = by + row * (btn_h + gap_y)
            button = xbmcgui.ControlButton(x, y, btn_w, btn_h, label, alignment=6)
            self.addControl(button)
            self._buttons[button_id] = button

        buttons = self._buttons
        buttons[self._BTN_1].setNavigation(buttons[self._BTN_DEL], buttons[self._BTN_4], buttons[self._BTN_3], buttons[self._BTN_2])
        buttons[self._BTN_2].setNavigation(buttons[self._BTN_0], buttons[self._BTN_5], buttons[self._BTN_1], buttons[self._BTN_3])
        buttons[self._BTN_3].setNavigation(buttons[self._BTN_OK], buttons[self._BTN_6], buttons[self._BTN_2], buttons[self._BTN_1])
        buttons[self._BTN_4].setNavigation(buttons[self._BTN_1], buttons[self._BTN_7], buttons[self._BTN_6], buttons[self._BTN_5])
        buttons[self._BTN_5].setNavigation(buttons[self._BTN_2], buttons[self._BTN_8], buttons[self._BTN_4], buttons[self._BTN_6])
        buttons[self._BTN_6].setNavigation(buttons[self._BTN_3], buttons[self._BTN_9], buttons[self._BTN_5], buttons[self._BTN_4])
        buttons[self._BTN_7].setNavigation(buttons[self._BTN_4], buttons[self._BTN_DEL], buttons[self._BTN_9], buttons[self._BTN_8])
        buttons[self._BTN_8].setNavigation(buttons[self._BTN_5], buttons[self._BTN_0], buttons[self._BTN_7], buttons[self._BTN_9])
        buttons[self._BTN_9].setNavigation(buttons[self._BTN_6], buttons[self._BTN_OK], buttons[self._BTN_8], buttons[self._BTN_7])
        buttons[self._BTN_DEL].setNavigation(buttons[self._BTN_7], buttons[self._BTN_1], buttons[self._BTN_OK], buttons[self._BTN_0])
        buttons[self._BTN_0].setNavigation(buttons[self._BTN_8], buttons[self._BTN_2], buttons[self._BTN_DEL], buttons[self._BTN_OK])
        buttons[self._BTN_OK].setNavigation(buttons[self._BTN_9], buttons[self._BTN_3], buttons[self._BTN_0], buttons[self._BTN_DEL])
        self.setFocus(buttons[self._BTN_5])

    def _update_pin_display(self):
        self._pin_label.setLabel(self._pin if self._pin else '- - - -')

    def onClick(self, control_id):
        if control_id == self._BTN_DEL:
            self._pin = self._pin[:-1]
            self._update_pin_display()
        elif control_id == self._BTN_OK:
            self._confirmed = True
            self.close()
        elif control_id in self._DIGITS and len(self._pin) < 8:
            self._pin += self._DIGITS[control_id]
            self._update_pin_display()

    def onAction(self, action):
        if action.getId() in (self._ACTION_BACK, self._ACTION_BACK2):
            self._confirmed = False
            self.close()

    def get_input(self):
        self.doModal()
        return self._pin if self._confirmed else None


class BouncerPlayer(xbmc.Player):
    def __init__(self):
        super(BouncerPlayer, self).__init__()
        self.addon = xbmcaddon.Addon()
        self._lock = threading.Lock()
        self.unlocked_until = 0
        self.check_in_progress = False
        self.allow_once_key = ''
        self.current_playback_key = ''
        self._tmdb_cache = {}
        profile = xbmcvfs.translatePath(self.addon.getAddonInfo('profile'))
        os.makedirs(profile, exist_ok=True)
        self._whitelist_store = WhitelistStore.from_profile(profile)

    def onAVStarted(self):
        self.addon = xbmcaddon.Addon()
        if not self.addon.getSettingBool('enabled'):
            return

        with self._lock:
            if self.unlocked_until > time.time():
                log('Session unlocked - skipping PIN check')
                return
            if self.check_in_progress:
                log('Check already in progress - skipping')
                return
            if not self.isPlayingVideo():
                return
            self.check_in_progress = True

        thread = threading.Thread(target=self._check_and_gate, daemon=True)
        thread.start()

    def onPlayBackStopped(self):
        self._reset_runtime_access()

    def onPlayBackEnded(self):
        self._reset_runtime_access()

    def _reset_runtime_access(self):
        with self._lock:
            self.allow_once_key = ''
            self.current_playback_key = ''

    def _check_and_gate(self):
        try:
            self._do_check()
        finally:
            with self._lock:
                self.check_in_progress = False

    def _do_check(self):
        xbmc.sleep(1500)
        if not self.isPlayingVideo():
            log('Playback ended before check completed')
            return

        context = extract_playback_context(self)
        runtime_key = runtime_allow_key(context)
        with self._lock:
            if self.allow_once_key and self.allow_once_key != runtime_key:
                self.allow_once_key = ''
            self.current_playback_key = runtime_key
            if runtime_key and self.allow_once_key == runtime_key:
                log('One-time allow matched for "{}"'.format(context.display_title))
                return

        whitelist_hit = self._whitelist_store.find_match(context)
        if whitelist_hit:
            log(
                'Whitelist matched "{}" via {} scope'.format(
                    context.display_title, whitelist_hit['scope']
                ),
                xbmc.LOGINFO,
            )
            return

        canonical = resolve_rating(
            self.addon,
            context,
            self._tmdb_cache,
            lambda message: log(message, xbmc.LOGDEBUG),
        )
        if len(self._tmdb_cache) > _TMDB_CACHE_MAX:
            self._tmdb_cache.pop(next(iter(self._tmdb_cache)))

        should_block = False
        block_reason = ''
        if not canonical:
            unrated_action = self.addon.getSetting('unrated_action')
            if unrated_action == 'Allow':
                log('Rating unavailable for "{}" - allowing per settings'.format(
                    context.display_title
                ))
                return
            should_block = True
            if not self.addon.getSetting('tmdb_api_key').strip():
                block_reason = 'Rating unavailable (no TMDb key configured)'
            else:
                block_reason = 'Rating unavailable'
        else:
            blocked = is_rating_blocked(canonical, self.addon)
            if blocked is None:
                should_block = (self.addon.getSetting('unrated_action') == 'Block')
                block_reason = 'Unrecognized rating: {}'.format(canonical)
            elif blocked:
                should_block = True
                block_reason = 'Rated {}'.format(canonical)
            else:
                log('"{}" rated {} - allowed'.format(context.display_title, canonical))

        if not should_block:
            return

        log('Blocking "{}": {}'.format(context.display_title, block_reason), xbmc.LOGINFO)
        self._require_pin(context, block_reason, runtime_key)

    def _post_pin_actions(self, context):
        actions = [{'id': 'allow_once', 'label': 'Allow one time'}]
        unlock_mins = int(self.addon.getSetting('unlock_duration') or 0)
        if unlock_mins > 0:
            actions.append({
                'id': 'allow_session',
                'label': 'Allow for {} minute{}'.format(
                    unlock_mins, '' if unlock_mins == 1 else 's'
                ),
            })

        for scope in menu_scopes_for_context(context):
            entry = build_entry(context, scope)
            if not entry:
                continue
            if scope == 'movie':
                label = 'Whitelist this movie'
            elif scope == 'episode':
                label = 'Whitelist this episode'
            elif scope == 'season':
                label = 'Whitelist this season'
            else:
                label = 'Whitelist this show'
            actions.append({'id': 'whitelist', 'label': label, 'scope': scope})

        labels = [action['label'] for action in actions]
        selection = xbmcgui.Dialog().select(
            'Bouncer - Allow Access',
            labels,
        )
        if selection < 0:
            return {'id': 'allow_once'}
        return actions[selection]

    def _apply_access_action(self, action, context, runtime_key):
        action_id = action.get('id')
        if action_id == 'allow_session':
            unlock_mins = int(self.addon.getSetting('unlock_duration') or 0)
            with self._lock:
                self.unlocked_until = time.time() + (unlock_mins * 60)
                self.allow_once_key = ''
            log('Session unlocked for {} minutes'.format(unlock_mins), xbmc.LOGINFO)
            xbmcgui.Dialog().notification(
                'Bouncer',
                'Access allowed for {} minute{}'.format(
                    unlock_mins, '' if unlock_mins == 1 else 's'
                ),
                xbmcgui.NOTIFICATION_INFO,
                2000,
            )
            return

        if action_id == 'whitelist':
            status, pruned, entry = self._whitelist_store.add_context_scope(
                context, action.get('scope', '')
            )
            if status == 'added' and entry:
                message = 'Whitelisted {}'.format(entry['label'])
                if pruned:
                    message += ' (removed {} child entr{})'.format(
                        pruned, 'y' if pruned == 1 else 'ies'
                    )
                log('Added whitelist: {}'.format(entry['key']), xbmc.LOGINFO)
                xbmcgui.Dialog().notification(
                    'Bouncer',
                    message,
                    xbmcgui.NOTIFICATION_INFO,
                    2500,
                )
                return
            if status == 'covered':
                xbmcgui.Dialog().notification(
                    'Bouncer',
                    'Already covered by a broader whitelist',
                    xbmcgui.NOTIFICATION_INFO,
                    2500,
                )
                return
            if status == 'existing':
                xbmcgui.Dialog().notification(
                    'Bouncer',
                    'Already whitelisted',
                    xbmcgui.NOTIFICATION_INFO,
                    2500,
                )
                return

        with self._lock:
            self.allow_once_key = runtime_key
            self.unlocked_until = 0
        log('One-time allow granted for "{}"'.format(context.display_title), xbmc.LOGINFO)
        xbmcgui.Dialog().notification(
            'Bouncer',
            'Access granted once',
            xbmcgui.NOTIFICATION_INFO,
            2000,
        )

    def _require_pin(self, context, reason, runtime_key):
        self.pause()
        xbmc.sleep(200)

        dialog = PinDialog('Bouncer - PIN Required', reason)
        result = dialog.get_input()
        if result is None:
            log('PIN dialog cancelled - stopping playback', xbmc.LOGINFO)
            self.stop()
            xbmc.executebuiltin('ActivateWindow(Home)')
            return

        if result == self.addon.getSetting('pin_code'):
            log('Correct PIN entered - access menu opening', xbmc.LOGINFO)
            action = self._post_pin_actions(context)
            self._apply_access_action(action, context, runtime_key)
            self.pause()
            return

        log('Incorrect PIN entered - stopping playback', xbmc.LOGINFO)
        self.stop()
        xbmcgui.Dialog().ok(
            'Bouncer - Access Denied',
            '{}\n{}\n\nIncorrect PIN.'.format(context.display_title, reason),
        )
        xbmc.executebuiltin('ActivateWindow(Home)')


class BouncerMonitor(xbmc.Monitor):
    pass


if __name__ == '__main__':
    monitor = BouncerMonitor()
    player = BouncerPlayer()
    addon = xbmcaddon.Addon()
    log('Bouncer v{} started'.format(addon.getAddonInfo('version')), xbmc.LOGINFO)

    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break

    log('Bouncer stopped', xbmc.LOGINFO)
