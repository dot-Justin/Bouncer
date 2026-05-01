from dataclasses import dataclass

try:
    import xbmc
except ImportError:  # pragma: no cover - only used inside Kodi
    xbmc = None


@dataclass
class PlaybackContext:
    media_type: str = ''
    title: str = ''
    show_title: str = ''
    year: int = 0
    season: int = 0
    episode: int = 0
    display_title: str = ''
    lookup_title: str = ''
    lookup_year: int = 0
    file_path: str = ''
    dbid: int = 0
    imdb_id: str = ''
    tmdb_id: str = ''
    tvdb_id: str = ''
    mpaa: str = ''

    @property
    def is_tv(self):
        return self.media_type in ('episode', 'season', 'tvshow') or bool(self.show_title)


def normalize_text(value):
    return ' '.join((value or '').strip().lower().split())


def safe_int(value, default=0):
    try:
        if value in (None, ''):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def infer_media_type(show_title, season, episode):
    if show_title and season and episode:
        return 'episode'
    if show_title and season:
        return 'season'
    if show_title:
        return 'tvshow'
    return 'movie'


def finalize_context(context):
    if context.media_type == 'episode' and context.show_title:
        context.display_title = '{} S{:02d}E{:02d}'.format(
            context.show_title, context.season, context.episode
        )
        context.lookup_title = context.show_title
    elif context.media_type == 'season' and context.show_title:
        context.display_title = '{} Season {}'.format(
            context.show_title, context.season
        )
        context.lookup_title = context.show_title
    elif context.media_type == 'tvshow' and context.show_title:
        context.display_title = context.show_title
        context.lookup_title = context.show_title
    else:
        base_title = context.title or context.show_title
        if context.year:
            context.display_title = '{} ({})'.format(base_title, context.year)
        else:
            context.display_title = base_title
        context.lookup_title = base_title

    context.lookup_year = context.year or 0
    return context


def _tag_call(tag, method_name, default=''):
    if not tag:
        return default
    method = getattr(tag, method_name, None)
    if not callable(method):
        return default
    try:
        value = method()
    except TypeError:
        return default
    except Exception:
        return default
    if value is None:
        return default
    return value


def _tag_unique_id(tag, key):
    if not tag:
        return ''
    method = getattr(tag, 'getUniqueID', None)
    if not callable(method):
        return ''
    try:
        value = method(key)
    except Exception:
        return ''
    return (value or '').strip()


def extract_playback_context(player):
    if xbmc is None:  # pragma: no cover - only used inside Kodi
        raise RuntimeError('xbmc module unavailable')

    tag = None
    try:
        tag = player.getVideoInfoTag()
    except Exception:
        tag = None

    show_title = _tag_call(tag, 'getTVShowTitle', '').strip()
    title = _tag_call(tag, 'getTitle', '').strip()
    media_type = _tag_call(tag, 'getMediaType', '').strip().lower()
    year = safe_int(_tag_call(tag, 'getYear', 0))
    season = safe_int(_tag_call(tag, 'getSeason', 0))
    episode = safe_int(_tag_call(tag, 'getEpisode', 0))
    dbid = safe_int(_tag_call(tag, 'getDbId', 0))
    file_path = (
        _tag_call(tag, 'getFilenameAndPath', '').strip() or
        _tag_call(tag, 'getFile', '').strip()
    )
    imdb_id = (
        _tag_unique_id(tag, 'imdb') or
        _tag_call(tag, 'getIMDBNumber', '').strip() or
        xbmc.getInfoLabel('VideoPlayer.IMDBNumber').strip()
    )
    tmdb_id = _tag_unique_id(tag, 'tmdb')
    tvdb_id = _tag_unique_id(tag, 'tvdb')
    mpaa = xbmc.getInfoLabel('VideoPlayer.MPAA').strip()

    if not show_title:
        show_title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle').strip()
    if not title:
        title = xbmc.getInfoLabel('VideoPlayer.Title').strip()
    if not year:
        year = safe_int(xbmc.getInfoLabel('VideoPlayer.Year').strip())
    if not season:
        season = safe_int(xbmc.getInfoLabel('VideoPlayer.Season').strip())
    if not episode:
        episode = safe_int(xbmc.getInfoLabel('VideoPlayer.Episode').strip())
    if not media_type:
        media_type = infer_media_type(show_title, season, episode)

    context = PlaybackContext(
        media_type=media_type,
        title=title,
        show_title=show_title,
        year=year,
        season=season,
        episode=episode,
        file_path=file_path,
        dbid=dbid,
        imdb_id=imdb_id,
        tmdb_id=tmdb_id,
        tvdb_id=tvdb_id,
        mpaa=mpaa,
    )
    return finalize_context(context)
