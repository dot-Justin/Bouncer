"""
Bouncer — Rating-based playback control for Kodi
Addon ID: service.bouncer
Author:   dotJustin
Version:  1.0.0

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
import hashlib

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


def normalize_rating(raw):
    """Return the canonical rating key for a raw MPAA/TV rating string.

    Returns an empty string if the raw value is unrecognised (not the same as
    NR — empty means "we have no idea what this rating is").
    """
    if not raw:
        return ''
    candidate = RATING_NORM.get(raw.strip().upper(), '')
    return candidate


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

    IMPORTANT: Never pass PIN values to this function.
    """
    addon = xbmcaddon.Addon()
    if level >= xbmc.LOGINFO or addon.getSettingBool('debug_log'):
        xbmc.log('[Bouncer] {}'.format(msg), level)


# ---------------------------------------------------------------------------
# Player subclass
# ---------------------------------------------------------------------------

class BouncerPlayer(xbmc.Player):
    """Watches video playback and gates content behind a PIN when blocked."""

    def __init__(self):
        super(BouncerPlayer, self).__init__()
        self.addon = xbmcaddon.Addon()
        # Timestamp after which the session is considered unlocked (0 = locked)
        self.unlocked_until = 0
        # Guard against re-entrant checks if two events fire close together
        self.check_in_progress = False
        # In-memory cache: display_title / imdb_id → canonical rating string
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

        if self.unlocked_until > time.time():
            log('Session unlocked — skipping PIN check')
            return

        if self.check_in_progress:
            log('Check already in progress — skipping')
            return

        if not self.isPlayingVideo():
            return

        # Run the blocking check in a background thread so we never stall
        # Kodi's player thread (which would freeze the UI).
        t = threading.Thread(target=self._check_and_gate, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Main gating logic (runs in background thread)
    # ------------------------------------------------------------------

    def _check_and_gate(self):
        """Determine content rating and gate playback if necessary."""
        self.check_in_progress = True
        try:
            self._do_check()
        finally:
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

    def _fetch_tmdb_rating(self, imdb_id, display_title, is_tv, api_key):
        """Query the TMDb API and return a canonical rating string.

        Uses the IMDB ID when available; falls back to a title search.
        Returns an empty string if no US certification can be found.
        """
        def make_request(url):
            req = urllib_request.Request(url)
            req.add_header('User-Agent', 'Bouncer/1.0 Kodi Addon')
            with urllib_request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode('utf-8'))

        tmdb_id = None

        if imdb_id:
            # We already have an ID — use it directly
            if is_tv:
                ratings_url = (
                    'https://api.themoviedb.org/3/tv/{}/content_ratings'
                    '?api_key={}'.format(imdb_id, api_key)
                )
            else:
                ratings_url = (
                    'https://api.themoviedb.org/3/movie/{}/release_dates'
                    '?api_key={}'.format(imdb_id, api_key)
                )
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
        log('Method 3 API result: raw="{}" canonical="{}"'.format(cert, canonical))
        return canonical

    # ------------------------------------------------------------------
    # PIN gate
    # ------------------------------------------------------------------

    def _require_pin(self, display_title, reason):
        """Pause playback and present the PIN dialog.

        INPUT_NUMERIC renders as a D-pad-navigable number pad on Android TV,
        which is the only usable dialog type without a physical keyboard.
        """
        # Pause before showing the dialog
        xbmc.Player().pause()
        xbmc.sleep(200)

        result = xbmcgui.Dialog().input(
            'Bouncer \u2014 PIN Required',
            type=xbmcgui.INPUT_NUMERIC
        )

        # --- Cancelled (user pressed Back / closed dialog) ---------------
        if result == '':
            log('PIN dialog cancelled — stopping playback', xbmc.LOGINFO)
            xbmc.Player().stop()
            xbmc.executebuiltin('ActivateWindow(Home)')
            return

        # --- Correct PIN -------------------------------------------------
        if result == self.addon.getSetting('pin_code'):
            log('Correct PIN entered — access granted', xbmc.LOGINFO)
            unlock_mins = int(self.addon.getSetting('unlock_duration') or 0)
            if unlock_mins > 0:
                self.unlocked_until = time.time() + (unlock_mins * 60)
                log('Session unlocked for {} minutes'.format(unlock_mins))
            # Toggle pause to resume playback
            xbmc.Player().pause()
            xbmcgui.Dialog().notification(
                'Bouncer',
                'Access granted \u2713',
                xbmcgui.NOTIFICATION_INFO,
                2000
            )
            return

        # --- Incorrect PIN -----------------------------------------------
        log('Incorrect PIN entered — stopping playback', xbmc.LOGINFO)
        xbmc.Player().stop()
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
