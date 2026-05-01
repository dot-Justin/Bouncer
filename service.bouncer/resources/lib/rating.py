import json

try:
    import urllib.parse as urllib_parse
    import urllib.request as urllib_request
except ImportError:  # pragma: no cover - Kodi 21 uses Python 3
    import urllib as urllib_parse
    import urllib2 as urllib_request

try:
    import xbmc
except ImportError:  # pragma: no cover - only used inside Kodi
    xbmc = None


RATING_NORM = {
    'G': 'G',
    'RATED G': 'G',
    'PG': 'PG',
    'RATED PG': 'PG',
    'PG-13': 'PG13',
    'RATED PG-13': 'PG13',
    'PG13': 'PG13',
    'R': 'R',
    'RATED R': 'R',
    'NC-17': 'NC17',
    'RATED NC-17': 'NC17',
    'NC17': 'NC17',
    'NR': 'NR',
    'NOT RATED': 'NR',
    'UNRATED': 'NR',
    'UR': 'NR',
    'N/A': 'NR',
    'TV-Y': 'TVY',
    'TVY': 'TVY',
    'TV-Y7': 'TVY7',
    'TVY7': 'TVY7',
    'TV-G': 'TVG',
    'TVG': 'TVG',
    'TV-PG': 'TVPG',
    'TVPG': 'TVPG',
    'TV-14': 'TV14',
    'TV14': 'TV14',
    'TV-MA': 'TVMA',
    'TVMA': 'TVMA',
    'TV-NR': 'TVNR',
    'TVNR': 'TVNR',
}

RATING_SETTING_KEY = {
    'G': 'block_G',
    'PG': 'block_PG',
    'PG13': 'block_PG13',
    'R': 'block_R',
    'NC17': 'block_NC17',
    'NR': 'block_NR',
    'TVY': 'block_TVY',
    'TVY7': 'block_TVY7',
    'TVG': 'block_TVG',
    'TVPG': 'block_TVPG',
    'TV14': 'block_TV14',
    'TVMA': 'block_TVMA',
    'TVNR': 'block_TVNR',
}


def normalize_rating(raw):
    if not raw:
        return ''
    return RATING_NORM.get(raw.strip().upper(), '')


def is_rating_blocked(canonical, addon):
    setting_key = RATING_SETTING_KEY.get(canonical)
    if setting_key is None:
        return None
    return addon.getSettingBool(setting_key)


def build_tmdb_lookup_plan(context, imdb_id=''):
    if context.tmdb_id:
        return {
            'strategy': 'tmdb_id',
            'tmdb_id': context.tmdb_id,
            'is_tv': context.is_tv,
        }
    if imdb_id:
        return {
            'strategy': 'imdb_id',
            'imdb_id': imdb_id,
            'is_tv': context.is_tv,
        }
    if not context.lookup_title:
        return {
            'strategy': 'unavailable',
            'is_tv': context.is_tv,
        }
    return {
        'strategy': 'search',
        'query': context.lookup_title,
        'year': context.lookup_year or 0,
        'is_tv': context.is_tv,
    }


def tmdb_cache_key(context, imdb_id=''):
    plan = build_tmdb_lookup_plan(context, imdb_id)
    strategy = plan['strategy']
    if strategy == 'tmdb_id':
        return 'tmdb:{}:{}'.format('tv' if plan['is_tv'] else 'movie', plan['tmdb_id'])
    if strategy == 'imdb_id':
        return 'imdb:{}:{}'.format('tv' if plan['is_tv'] else 'movie', plan['imdb_id'])
    if strategy == 'search':
        return 'search:{}:{}:{}'.format(
            'tv' if plan['is_tv'] else 'movie',
            plan['query'].lower(),
            plan['year'],
        )
    return ''


def _safe_url(url):
    if 'api_key=' not in url:
        return url
    before, rest = url.split('api_key=', 1)
    end = rest.find('&')
    suffix = rest[end:] if end != -1 else ''
    return '{}api_key=***{}'.format(before, suffix)


def _make_request(url, log):
    req = urllib_request.Request(url)
    req.add_header('User-Agent', 'Bouncer/1.1 Kodi Addon')
    log('TMDb request: {}'.format(_safe_url(url)))
    with urllib_request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _resolve_tmdb_id(context, api_key, plan, log):
    if plan['strategy'] == 'tmdb_id':
        return plan['tmdb_id']
    if plan['strategy'] == 'imdb_id':
        find_url = (
            'https://api.themoviedb.org/3/find/{}'
            '?api_key={}&external_source=imdb_id'.format(plan['imdb_id'], api_key)
        )
        find_data = _make_request(find_url, log)
        key = 'tv_results' if plan['is_tv'] else 'movie_results'
        results = find_data.get(key, [])
        if not results:
            raise ValueError('No TMDb result for IMDb ID {}'.format(plan['imdb_id']))
        return str(results[0]['id'])
    if plan['strategy'] == 'search':
        query = urllib_parse.quote(plan['query'])
        if plan['is_tv']:
            search_url = (
                'https://api.themoviedb.org/3/search/tv'
                '?api_key={}&query={}'.format(api_key, query)
            )
        else:
            search_url = (
                'https://api.themoviedb.org/3/search/movie'
                '?api_key={}&query={}'.format(api_key, query)
            )
            if plan.get('year'):
                search_url += '&year={}'.format(plan['year'])
        search_data = _make_request(search_url, log)
        results = search_data.get('results', [])
        if not results:
            raise ValueError('No TMDb search results for "{}"'.format(plan['query']))
        return str(results[0]['id'])
    return ''


def _fetch_tmdb_rating(context, api_key, imdb_id, log):
    plan = build_tmdb_lookup_plan(context, imdb_id)
    tmdb_id = _resolve_tmdb_id(context, api_key, plan, log)
    if not tmdb_id:
        return ''

    if plan['is_tv']:
        ratings_url = (
            'https://api.themoviedb.org/3/tv/{}/content_ratings'
            '?api_key={}'.format(tmdb_id, api_key)
        )
    else:
        ratings_url = (
            'https://api.themoviedb.org/3/movie/{}/release_dates'
            '?api_key={}'.format(tmdb_id, api_key)
        )

    data = _make_request(ratings_url, log)
    cert = ''
    for entry in data.get('results', []):
        if entry.get('iso_3166_1') != 'US':
            continue
        if plan['is_tv']:
            cert = entry.get('rating', '')
        else:
            for release in entry.get('release_dates', []):
                if release.get('certification'):
                    cert = release['certification']
                    break
        break
    return normalize_rating(cert)


def _jsonrpc_player_item(log):
    if xbmc is None:  # pragma: no cover - only used inside Kodi
        return {}
    try:
        players_resp = json.loads(xbmc.executeJSONRPC(
            '{"jsonrpc":"2.0","method":"Player.GetActivePlayers","id":1}'
        ))
        players = players_resp.get('result', [])
        video_player = next((p for p in players if p.get('type') == 'video'), None)
        if not video_player:
            return {}
        item_resp = json.loads(xbmc.executeJSONRPC(json.dumps({
            'jsonrpc': '2.0',
            'method': 'Player.GetItem',
            'params': {
                'playerid': video_player['playerid'],
                'properties': [
                    'mpaa', 'imdbnumber', 'title', 'year',
                    'showtitle', 'season', 'episode', 'type'
                ],
            },
            'id': 2,
        })))
        return item_resp.get('result', {}).get('item', {})
    except Exception as exc:
        log('Player.GetItem failed: {}'.format(exc))
        return {}


def resolve_rating(addon, context, tmdb_cache, log):
    raw = context.mpaa
    canonical = normalize_rating(raw)
    if canonical:
        log('Rating via VideoPlayer.MPAA: raw="{}" canonical="{}"'.format(raw, canonical))
        return canonical

    imdb_id = context.imdb_id
    item = _jsonrpc_player_item(log)
    if item:
        raw = (item.get('mpaa') or '').strip()
        canonical = normalize_rating(raw)
        imdb_id = imdb_id or (item.get('imdbnumber') or '').strip()
        log('Rating via Player.GetItem: raw="{}" canonical="{}" imdb="{}"'.format(
            raw, canonical, imdb_id
        ))
        if canonical:
            return canonical

    tmdb_api_key = addon.getSetting('tmdb_api_key').strip()
    if not tmdb_api_key:
        return ''

    cache_key = tmdb_cache_key(context, imdb_id)
    if cache_key and cache_key in tmdb_cache:
        canonical = tmdb_cache[cache_key]
        log('Rating via TMDb cache: "{}"'.format(canonical))
        return canonical

    try:
        canonical = _fetch_tmdb_rating(context, tmdb_api_key, imdb_id, log)
    except Exception as exc:
        log('TMDb rating lookup failed: {}'.format(exc))
        return ''

    if cache_key:
        tmdb_cache[cache_key] = canonical
    log('Rating via TMDb: canonical="{}"'.format(canonical))
    return canonical
