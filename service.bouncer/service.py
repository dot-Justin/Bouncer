"""
Bouncer — Rating-based playback control for Kodi
Addon ID: service.bouncer
Author:   dotJustin
Version:  1.0.2

Intercepts video playback and requires a PIN to continue if the content's
rating is in the user's blocked list. Designed for Kodi 21 Omega on Android TV
but compatible with any Kodi video source.
"""

import xbmc
import xbmcaddon
import xbmcgui
import json
import time
import threading

try:
    import urllib.request as urllib_request
    import urllib.parse as urllib_parse
except ImportError:
    # Python 2 fallback (Kodi < 19, should not be needed for Omega)
    import urllib2 as urllib_request
    import urllib as urllib_parse


# ---------------------------------------------------------------------------
# Rating normalisation tables
# ---------------------------------------------------------------------------

# Maps every raw rating string we might encounter → canonical key
RATING_NORM = {
    # Movie ratings
    'G': 'G',           'RATED G': 'G',
    'PG': 'PG',         'RATED PG': 'PG',
    'PG-13': 'PG13',    'RATED PG-13': 'PG13',  'PG13': 'PG13',
    'R': 'R',           'RATED R': 'R',
    'NC-17': 'NC17',    'RATED NC-17': 'NC17',   'NC17': 'NC17',
    'NR': 'NR',         'NOT RATED': 'NR',       'UNRATED': 'NR',
    'UR': 'NR',         'N/A': 'NR',             '': 'NR',
    # TV ratings
    'TV-Y': 'TVY',      'TVY': 'TVY',
    'TV-Y7': 'TVY7',    'TVY7': 'TVY7',
    'TV-G': 'TVG',      'TVG': 'TVG',
    'TV-PG': 'TVPG',    'TVPG': 'TVPG',
    'TV-14': 'TV14',    'TV14': 'TV14',
    'TV-MA': 'TVMA',    'TVMA': 'TVMA',
    'TV-NR': 'TVNR',    'TVNR': 'TVNR',
}

# Maps canonical key → settings.xml setting id
RATING_SETTING_KEY = {
    'G':    'block_G',
    'PG':   'block_PG',
    'PG13': 'block_PG13',
    'R':    'block_R',
    'NC17': 'block_NC17',
    'NR':   'block_NR',
    'TVY':  'block_TVY',
    'TVY7': 'block_TVY7',
    'TVG':  'block_TVG',
    'TVPG': 'block_TVPG',
    'TV14': 'block_TV14',
    'TVMA': 'block_TVMA',
    'TVNR': 'block_TVNR',
}

# Maximum number of TMDb lookup results to keep in memory
_TMDB_CACHE_MAX = 200


def normalize_rating(raw):
    """Return the canonical rating key for a raw MPAA/TV rating string.

    Returns an empty string if the raw value is unrecognised (not the same as
    NR — empty means "we have no idea what this rating is").
    """
    if not raw:
        return ''
    return RATING_NORM.get(raw.strip().upper(), '')


def is_rating_blocked(canonical, addon):
    """Check whether a canonical rating is blocked in user settings.

    Returns:
        True   — rating is blocked (PIN required)
        False  — rating is explicitly allowed
        None   — canonical key not found in our table (unknown rating)
    """
    setting_key = RATING_SETTING_KEY.get(canonical)
    if setting_key is None:
        return None
    return addon.getSettingBool(setting_key)


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def log(msg, level=xbmc.LOGDEBUG):
    """Log a message prefixed with [Bouncer].

    DEBUG messages are suppressed unless debug_log is enabled in settings.
    INFO and above always log.

    IMPORTANT: Never pass PIN values or raw API keys to this function.
    """
    addon = xbmcaddon.Addon()
    if level >= xbmc.LOGINFO or addon.getSettingBool('debug_log'):
        xbmc.log('[Bouncer] {}'.format(msg), level)


# ---------------------------------------------------------------------------
# PIN dialog
# ---------------------------------------------------------------------------

class PinDialog(xbmcgui.WindowDialog):
    """D-pad-navigable PIN entry dialog that displays the block reason.

    Layout (1280x720 virtual coordinates):

        ┌──────────────────────────────────────┐
        │  Bouncer — PIN Required              │
        │  Rated TV-MA                         │  ← reason
        │                                      │
        │           1 2 3 4                    │  ← PIN display
        │                                      │
        │      [1]      [2]      [3]           │
        │      [4]      [5]      [6]           │
        │      [7]      [8]      [9]           │
        │      [⌫]      [0]     [OK]           │
        └──────────────────────────────────────┘

    Returns the entered string via get_input(), or None if cancelled.
    """

    # --- Button control IDs -------------------------------------------------
    _BTN_1   = 100
    _BTN_2   = 101
    _BTN_3   = 102
    _BTN_4   = 103
    _BTN_5   = 104
    _BTN_6   = 105
    _BTN_7   = 106
    _BTN_8   = 107
    _BTN_9   = 108
    _BTN_DEL = 109
    _BTN_0   = 110
    _BTN_OK  = 111

    # Kodi action IDs
    _ACTION_BACK  = 10
    _ACTION_BACK2 = 92

    # Digit map: button ID → character
    _DIGITS = {
        _BTN_1: '1', _BTN_2: '2', _BTN_3: '3',
        _BTN_4: '4', _BTN_5: '5', _BTN_6: '6',
        _BTN_7: '7', _BTN_8: '8', _BTN_9: '9',
        _BTN_0: '0',
    }

    def __init__(self, heading, reason):
        super(PinDialog, self).__init__()
        self._heading   = heading
        self._reason    = reason
        self._pin       = ''
        self._confirmed = False
        self._pin_label = None
        self._buttons   = {}
        self._build()

    # ------------------------------------------------------------------
    # Build controls
    # ------------------------------------------------------------------

    def _build(self):
        # Virtual screen size Kodi uses for layout
        sw, sh = 1280, 720
        # Dialog box dimensions
        dw, dh = 660, 530
        dx = (sw - dw) // 2   # 310
        dy = (sh - dh) // 2   # 95

        # --- Background overlay (semi-transparent black) ---
        # An empty filename with colorDiffuse renders a tinted overlay on most
        # skins; if the skin can't resolve it the dialog still works fine.
        self.addControl(xbmcgui.ControlImage(
            0, 0, sw, sh, '',
            colorDiffuse='BB000000'
        ))

        # --- Dialog box background ---
        self.addControl(xbmcgui.ControlImage(
            dx, dy, dw, dh, '',
            colorDiffuse='F0121220'
        ))

        # --- Heading ---
        self.addControl(xbmcgui.ControlLabel(
            dx + 20, dy + 22, dw - 40, 48,
            self._heading,
            font='font14',
            textColor='FFFFFFFF',
            alignment=6   # centred
        ))

        # --- Reason / subtitle ---
        self.addControl(xbmcgui.ControlLabel(
            dx + 20, dy + 74, dw - 40, 38,
            self._reason,
            font='font13',
            textColor='FF9999BB',
            alignment=6
        ))

        # --- Divider ---
        self.addControl(xbmcgui.ControlImage(
            dx + 30, dy + 118, dw - 60, 2, '',
            colorDiffuse='55FFFFFF'
        ))

        # --- PIN display ---
        self._pin_label = xbmcgui.ControlLabel(
            dx + 20, dy + 130, dw - 40, 60,
            '- - - -',
            font='font30',
            textColor='FFFFFFFF',
            alignment=6
        )
        self.addControl(self._pin_label)

        # --- Number pad ---
        btn_w, btn_h = 140, 65
        gap_x, gap_y = 18, 10
        grid_w = 3 * btn_w + 2 * gap_x        # 452
        bx = dx + (dw - grid_w) // 2          # 414
        by = dy + 215

        pad_layout = [
            (self._BTN_1, '1',  0, 0),
            (self._BTN_2, '2',  1, 0),
            (self._BTN_3, '3',  2, 0),
            (self._BTN_4, '4',  0, 1),
            (self._BTN_5, '5',  1, 1),
            (self._BTN_6, '6',  2, 1),
            (self._BTN_7, '7',  0, 2),
            (self._BTN_8, '8',  1, 2),
            (self._BTN_9, '9',  2, 2),
            (self._BTN_DEL, '\u232b', 0, 3),  # ⌫
            (self._BTN_0,  '0',       1, 3),
            (self._BTN_OK, 'OK',      2, 3),
        ]

        for bid, label, col, row in pad_layout:
            x = bx + col * (btn_w + gap_x)
            y = by + row * (btn_h + gap_y)
            btn = xbmcgui.ControlButton(x, y, btn_w, btn_h, label, alignment=6)
            self.addControl(btn)
            self._buttons[bid] = btn

        # --- D-pad navigation ---
        # setNavigation(up, down, left, right)
        b = self._buttons
        b[self._BTN_1].setNavigation(b[self._BTN_DEL], b[self._BTN_4], b[self._BTN_3],   b[self._BTN_2])
        b[self._BTN_2].setNavigation(b[self._BTN_0],   b[self._BTN_5], b[self._BTN_1],   b[self._BTN_3])
        b[self._BTN_3].setNavigation(b[self._BTN_OK],  b[self._BTN_6], b[self._BTN_2],   b[self._BTN_1])
        b[self._BTN_4].setNavigation(b[self._BTN_1],   b[self._BTN_7], b[self._BTN_6],   b[self._BTN_5])
        b[self._BTN_5].setNavigation(b[self._BTN_2],   b[self._BTN_8], b[self._BTN_4],   b[self._BTN_6])
        b[self._BTN_6].setNavigation(b[self._BTN_3],   b[self._BTN_9], b[self._BTN_5],   b[self._BTN_4])
        b[self._BTN_7].setNavigation(b[self._BTN_4],   b[self._BTN_DEL], b[self._BTN_9], b[self._BTN_8])
        b[self._BTN_8].setNavigation(b[self._BTN_5],   b[self._BTN_0], b[self._BTN_7],   b[self._BTN_9])
        b[self._BTN_9].setNavigation(b[self._BTN_6],   b[self._BTN_OK], b[self._BTN_8],  b[self._BTN_7])
        b[self._BTN_DEL].setNavigation(b[self._BTN_7], b[self._BTN_1], b[self._BTN_OK],  b[self._BTN_0])
        b[self._BTN_0].setNavigation(  b[self._BTN_8], b[self._BTN_2], b[self._BTN_DEL], b[self._BTN_OK])
        b[self._BTN_OK].setNavigation( b[self._BTN_9], b[self._BTN_3], b[self._BTN_0],   b[self._BTN_DEL])

        # Start focus on 5 (centre of pad)
        self.setFocus(b[self._BTN_5])

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _update_pin_display(self):
        self._pin_label.setLabel(self._pin if self._pin else '- - - -')

    def onClick(self, control_id):
        if control_id == self._BTN_DEL:
            self._pin = self._pin[:-1]
            self._update_pin_display()
        elif control_id == self._BTN_OK:
            self._confirmed = True
            self.close()
        elif control_id in self._DIGITS:
            if len(self._pin) < 8:
                self._pin += self._DIGITS[control_id]
                self._update_pin_display()

    def onAction(self, action):
        if action.getId() in (self._ACTION_BACK, self._ACTION_BACK2):
            self._confirmed = False
            self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_input(self):
        """Show the dialog modally. Returns the entered PIN string, or None if cancelled."""
        self.doModal()
        return self._pin if self._confirmed else None


# ---------------------------------------------------------------------------
# Player subclass
# ---------------------------------------------------------------------------

class BouncerPlayer(xbmc.Player):
    """Watches video playback and gates content behind a PIN when blocked."""

    def __init__(self):
        super(BouncerPlayer, self).__init__()
        self.addon = xbmcaddon.Addon()
        # Lock protecting both unlocked_until and check_in_progress, which are
        # read on Kodi's player thread and written on background threads.
        self._lock = threading.Lock()
        # Timestamp after which the session is considered unlocked (0 = locked)
        self.unlocked_until = 0
        # Guard against re-entrant checks if two events fire close together
        self.check_in_progress = False
        # In-memory cache: imdb_id or display_title → canonical rating string
        # Evicted when it exceeds _TMDB_CACHE_MAX entries (oldest-first).
        self._tmdb_cache = {}

    # ------------------------------------------------------------------
    # Kodi player callback
    # ------------------------------------------------------------------

    def onAVStarted(self):
        """Called by Kodi when the AV stream is ready and data is flowing.

        We use onAVStarted instead of onPlayBackStarted because streaming
        addons (Umbrella, TMDbHelper) resolve stream URLs asynchronously.
        onPlayBackStarted fires before the stream is ready; onAVStarted fires
        once actual audio/video data is flowing, which is also when Kodi
        starts populating VideoPlayer.* infolabels.
        """
        # Reload settings on every playback event so changes take effect
        # without restarting the service.
        self.addon = xbmcaddon.Addon()

        if not self.addon.getSettingBool('enabled'):
            return

        # Hold the lock while reading both flags and setting check_in_progress,
        # so we never spawn two concurrent checks or miss an unlock.
        with self._lock:
            if self.unlocked_until > time.time():
                log('Session unlocked — skipping PIN check')
                return
            if self.check_in_progress:
                log('Check already in progress — skipping')
                return
            if not self.isPlayingVideo():
                return
            self.check_in_progress = True

        # Run the blocking check in a background thread so we never stall
        # Kodi's player thread (which would freeze the UI).
        t = threading.Thread(target=self._check_and_gate, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Main gating logic (runs in background thread)
    # ------------------------------------------------------------------

    def _check_and_gate(self):
        """Determine content rating and gate playback if necessary."""
        try:
            self._do_check()
        finally:
            with self._lock:
                self.check_in_progress = False

    def _do_check(self):
        # ------------------------------------------------------------------
        # Step 1 — Wait for infolabels to populate
        # ------------------------------------------------------------------
        # Even though onAVStarted fires when data is flowing, Kodi's infolabel
        # system populates VideoPlayer.* asynchronously from the ListItem
        # metadata. 1500 ms is the community-established safe wait value.
        xbmc.sleep(1500)

        if not self.isPlayingVideo():
            log('Playback ended before check completed')
            return

        # ------------------------------------------------------------------
        # Step 2 — Determine content type
        # ------------------------------------------------------------------
        is_tv = bool(xbmc.getInfoLabel('VideoPlayer.TVShowTitle').strip())

        # ------------------------------------------------------------------
        # Step 3 — Build a human-readable display title for dialogs/logging
        # ------------------------------------------------------------------
        if is_tv:
            show_title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle').strip()
            season = xbmc.getInfoLabel('VideoPlayer.Season').strip()
            episode = xbmc.getInfoLabel('VideoPlayer.Episode').strip()
            if season and episode:
                display_title = '{} S{}E{}'.format(show_title, season, episode)
            else:
                display_title = show_title
        else:
            title = xbmc.getInfoLabel('VideoPlayer.Title').strip()
            year = xbmc.getInfoLabel('VideoPlayer.Year').strip()
            display_title = '{} ({})'.format(title, year) if year else title

        log('Checking: "{}"  is_tv={}'.format(display_title, is_tv))

        # ------------------------------------------------------------------
        # Step 4 — Determine rating via three methods in priority order
        # ------------------------------------------------------------------
        canonical = ''
        imdb_id = ''

        # -- Method 1: VideoPlayer.MPAA infolabel --------------------------
        # This works when the playing addon passes mpaa via ListItem.setInfo().
        # Umbrella and TMDbHelper both do this for well-matched content.
        raw = xbmc.getInfoLabel('VideoPlayer.MPAA').strip()
        canonical = normalize_rating(raw)
        log('Method 1 rating: raw="{}" canonical="{}"'.format(raw, canonical))

        # -- Method 2: JSON-RPC Player.GetItem -----------------------------
        # Fallback for library content and cases where Method 1 is empty.
        if not canonical:
            try:
                players_resp = json.loads(xbmc.executeJSONRPC(
                    '{"jsonrpc":"2.0","method":"Player.GetActivePlayers","id":1}'
                ))
                players = players_resp.get('result', [])
                video_player = next(
                    (p for p in players if p.get('type') == 'video'), None
                )

                if video_player:
                    playerid = video_player['playerid']
                    item_resp = json.loads(xbmc.executeJSONRPC(json.dumps({
                        'jsonrpc': '2.0',
                        'method': 'Player.GetItem',
                        'params': {
                            'playerid': playerid,
                            'properties': [
                                'mpaa', 'imdbnumber', 'title', 'year',
                                'showtitle', 'season', 'episode', 'type'
                            ]
                        },
                        'id': 2
                    })))
                    item = item_resp.get('result', {}).get('item', {})
                    mpaa = item.get('mpaa', '').strip()
                    canonical = normalize_rating(mpaa)
                    imdb_id = item.get('imdbnumber', '').strip()
                    log('Method 2 rating: raw="{}" canonical="{}" imdb="{}"'.format(
                        mpaa, canonical, imdb_id
                    ))
            except Exception as exc:
                log('Method 2 error: {}'.format(exc), xbmc.LOGWARNING)

        # -- Method 3: TMDb API --------------------------------------------
        # Last resort for fully dynamic streams with no embedded metadata.
        # Only attempted when an API key is configured.
        tmdb_api_key = self.addon.getSetting('tmdb_api_key').strip()
        if not canonical and tmdb_api_key:
            try:
                # Try to get IMDB ID from infolabel if JSON-RPC didn't have it
                if not imdb_id:
                    imdb_id = xbmc.getInfoLabel('VideoPlayer.IMDBNumber').strip()

                cache_key = imdb_id or display_title
                if cache_key in self._tmdb_cache:
                    canonical = self._tmdb_cache[cache_key]
                    log('Method 3 cache hit: "{}"'.format(canonical))
                else:
                    canonical = self._fetch_tmdb_rating(
                        imdb_id, display_title, is_tv, tmdb_api_key
                    )
                    self._tmdb_cache[cache_key] = canonical
                    # Evict the oldest entry if the cache has grown too large
                    if len(self._tmdb_cache) > _TMDB_CACHE_MAX:
                        self._tmdb_cache.pop(next(iter(self._tmdb_cache)))

            except Exception as exc:
                log('Method 3 error: {}'.format(exc), xbmc.LOGWARNING)

        # ------------------------------------------------------------------
        # Step 5 — Evaluate and act
        # ------------------------------------------------------------------
        should_block = False
        block_reason = ''

        if not canonical:
            # Rating could not be determined at all
            unrated_action = self.addon.getSetting('unrated_action')
            if unrated_action == 'Allow':
                log('Rating undetermined for "{}", allowing per settings'.format(
                    display_title
                ))
                return
            else:
                should_block = True
                block_reason = 'Rating could not be determined'
        else:
            blocked = is_rating_blocked(canonical, self.addon)
            if blocked is None:
                # canonical came back but is not in our known-ratings table
                unrated_action = self.addon.getSetting('unrated_action')
                should_block = (unrated_action == 'Block')
                block_reason = 'Unrecognized rating: {}'.format(canonical)
            elif blocked:
                should_block = True
                block_reason = 'Rated {}'.format(canonical)
            else:
                log('"{}" rated {} — allowed'.format(display_title, canonical))

        if not should_block:
            return

        log('Blocking "{}": {}'.format(display_title, block_reason), xbmc.LOGINFO)
        self._require_pin(display_title, block_reason)

    # ------------------------------------------------------------------
    # TMDb API helper
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_url(url):
        """Return url with the api_key value redacted for safe debug logging."""
        if 'api_key=' not in url:
            return url
        before, rest = url.split('api_key=', 1)
        end = rest.find('&')
        suffix = rest[end:] if end != -1 else ''
        return '{}api_key=***{}'.format(before, suffix)

    def _fetch_tmdb_rating(self, imdb_id, display_title, is_tv, api_key):
        """Query the TMDb API and return a canonical rating string.

        When an IMDB ID is available (e.g. tt1234567), uses TMDb's Find
        endpoint to resolve it to a TMDb integer ID first — IMDB IDs cannot
        be used directly in the /tv/{id}/ or /movie/{id}/ endpoints.
        Falls back to a title search when no ID is available.
        Returns an empty string if no US certification can be found.
        """
        def make_request(url):
            req = urllib_request.Request(url)
            req.add_header('User-Agent', 'Bouncer/1.0 Kodi Addon')
            log('Method 3 request: {}'.format(self._safe_url(url)))
            with urllib_request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode('utf-8'))

        tmdb_id = None

        if imdb_id:
            # Use TMDb's Find endpoint to convert the IMDB ID (tt-prefixed
            # string) into a TMDb integer ID before fetching ratings.
            find_url = (
                'https://api.themoviedb.org/3/find/{}'
                '?api_key={}&external_source=imdb_id'.format(imdb_id, api_key)
            )
            find_data = make_request(find_url)
            key = 'tv_results' if is_tv else 'movie_results'
            results = find_data.get(key, [])
            if not results:
                raise ValueError('No TMDb results for IMDB ID {}'.format(imdb_id))
            tmdb_id = str(results[0]['id'])
        else:
            # No ID — search by title
            year_str = xbmc.getInfoLabel('VideoPlayer.Year').strip()
            title_enc = urllib_parse.quote(display_title)

            if is_tv:
                search_url = (
                    'https://api.themoviedb.org/3/search/tv'
                    '?api_key={}&query={}'.format(api_key, title_enc)
                )
            else:
                search_url = (
                    'https://api.themoviedb.org/3/search/movie'
                    '?api_key={}&query={}&year={}'.format(api_key, title_enc, year_str)
                )

            search_data = make_request(search_url)
            results = search_data.get('results', [])
            if not results:
                raise ValueError('No TMDb search results for "{}"'.format(display_title))
            tmdb_id = str(results[0]['id'])

        # Fetch US certification using the resolved TMDb integer ID
        if is_tv:
            ratings_url = (
                'https://api.themoviedb.org/3/tv/{}/content_ratings'
                '?api_key={}'.format(tmdb_id, api_key)
            )
        else:
            ratings_url = (
                'https://api.themoviedb.org/3/movie/{}/release_dates'
                '?api_key={}'.format(tmdb_id, api_key)
            )

        data = make_request(ratings_url)

        # Extract US certification
        cert = ''
        for entry in data.get('results', []):
            if entry.get('iso_3166_1') == 'US':
                if is_tv:
                    cert = entry.get('rating', '')
                else:
                    for d in entry.get('release_dates', []):
                        if d.get('certification'):
                            cert = d['certification']
                            break
                break

        canonical = normalize_rating(cert)
        log('Method 3 result: raw="{}" canonical="{}"'.format(cert, canonical))
        return canonical

    # ------------------------------------------------------------------
    # PIN gate
    # ------------------------------------------------------------------

    def _require_pin(self, display_title, reason):
        """Pause playback and present the custom PIN dialog."""
        self.pause()
        xbmc.sleep(200)

        dialog = PinDialog('Bouncer \u2014 PIN Required', reason)
        result = dialog.get_input()

        # --- Cancelled (Back button) --------------------------------------
        if result is None:
            log('PIN dialog cancelled — stopping playback', xbmc.LOGINFO)
            self.stop()
            xbmc.executebuiltin('ActivateWindow(Home)')
            return

        # --- Correct PIN --------------------------------------------------
        if result == self.addon.getSetting('pin_code'):
            log('Correct PIN entered — access granted', xbmc.LOGINFO)
            unlock_mins = int(self.addon.getSetting('unlock_duration') or 0)
            if unlock_mins > 0:
                with self._lock:
                    self.unlocked_until = time.time() + (unlock_mins * 60)
                log('Session unlocked for {} minutes'.format(unlock_mins))
            self.pause()  # toggle back to playing
            xbmcgui.Dialog().notification(
                'Bouncer',
                'Access granted \u2713',
                xbmcgui.NOTIFICATION_INFO,
                2000
            )
            return

        # --- Incorrect PIN ------------------------------------------------
        log('Incorrect PIN entered — stopping playback', xbmc.LOGINFO)
        self.stop()
        xbmcgui.Dialog().ok(
            'Bouncer \u2014 Access Denied',
            '{}\n{}\n\nIncorrect PIN.'.format(display_title, reason)
        )
        xbmc.executebuiltin('ActivateWindow(Home)')


# ---------------------------------------------------------------------------
# Monitor subclass
# ---------------------------------------------------------------------------

class BouncerMonitor(xbmc.Monitor):
    """Standard Kodi monitor — keeps the service alive and handles abort."""
    pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    monitor = BouncerMonitor()
    player = BouncerPlayer()
    addon = xbmcaddon.Addon()
    log('Bouncer v{} started'.format(addon.getAddonInfo('version')), xbmc.LOGINFO)

    # Run until Kodi signals an abort (shutdown / reboot)
    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break

    log('Bouncer stopped', xbmc.LOGINFO)
