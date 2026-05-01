from datetime import datetime, timezone
import json
import os

from .playback import normalize_text


SCHEMA_VERSION = 1
SCOPE_ORDER = ('movie', 'show', 'season', 'episode')
SCOPE_LABELS = {
    'movie': 'Movies',
    'show': 'Shows',
    'season': 'Seasons',
    'episode': 'Episodes',
}


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _movie_identity(context):
    title_norm = normalize_text(context.title)
    year = int(context.year or 0)
    if context.tmdb_id:
        return 'tmdb', context.tmdb_id.strip(), title_norm, year
    if context.imdb_id:
        return 'imdb', context.imdb_id.strip(), title_norm, year
    if title_norm:
        return 'title', '{}:{}'.format(title_norm, year or '0'), title_norm, year
    return '', '', title_norm, year


def _show_identity(context):
    show_norm = normalize_text(context.show_title or context.title)
    if context.tvdb_id:
        return 'tvdb', context.tvdb_id.strip(), show_norm
    if context.tmdb_id:
        return 'tmdb', context.tmdb_id.strip(), show_norm
    if context.imdb_id:
        return 'imdb', context.imdb_id.strip(), show_norm
    if show_norm:
        return 'title', show_norm, show_norm
    return '', '', show_norm


def _entry_key(scope, matcher):
    if scope == 'movie':
        if matcher.get('id_value'):
            return 'movie:{}:{}'.format(matcher['id_type'], matcher['id_value'])
        return 'movie:title:{}:{}'.format(
            matcher.get('title_norm', ''),
            matcher.get('year') or 0,
        )
    if scope == 'show':
        if matcher.get('id_value'):
            return 'show:{}:{}'.format(matcher['id_type'], matcher['id_value'])
        return 'show:title:{}'.format(matcher.get('show_title_norm', ''))
    if scope == 'season':
        prefix = _entry_key('show', matcher)
        return '{}:season:{}'.format(prefix, matcher['season'])
    if scope == 'episode':
        prefix = _entry_key('show', matcher)
        return '{}:season:{}:episode:{}'.format(
            prefix, matcher['season'], matcher['episode']
        )
    raise ValueError('Unsupported scope: {}'.format(scope))


def build_entry(context, scope, now=None):
    now = now or _utc_now()
    if scope == 'movie':
        if context.is_tv:
            return None
        id_type, id_value, title_norm, year = _movie_identity(context)
        if not id_value and not title_norm:
            return None
        matcher = {
            'kind': 'movie',
            'id_type': id_type,
            'id_value': id_value,
            'title_norm': title_norm,
            'year': year,
        }
        label = context.display_title or context.title or 'Unknown movie'
    elif scope == 'show':
        if not context.is_tv:
            return None
        id_type, id_value, show_title_norm = _show_identity(context)
        if not id_value and not show_title_norm:
            return None
        matcher = {
            'kind': 'show',
            'id_type': id_type,
            'id_value': id_value,
            'show_title_norm': show_title_norm,
        }
        label = context.show_title or context.display_title or 'Unknown show'
    elif scope == 'season':
        if not context.is_tv or not context.season:
            return None
        id_type, id_value, show_title_norm = _show_identity(context)
        if not id_value and not show_title_norm:
            return None
        matcher = {
            'kind': 'show',
            'id_type': id_type,
            'id_value': id_value,
            'show_title_norm': show_title_norm,
            'season': int(context.season),
        }
        label = '{} Season {}'.format(
            context.show_title or 'Unknown show',
            context.season,
        )
    elif scope == 'episode':
        if not context.is_tv or not context.season or not context.episode:
            return None
        id_type, id_value, show_title_norm = _show_identity(context)
        if not id_value and not show_title_norm:
            return None
        matcher = {
            'kind': 'show',
            'id_type': id_type,
            'id_value': id_value,
            'show_title_norm': show_title_norm,
            'season': int(context.season),
            'episode': int(context.episode),
        }
        label = context.display_title or '{} S{:02d}E{:02d}'.format(
            context.show_title or 'Unknown show',
            context.season,
            context.episode,
        )
    else:
        return None

    return {
        'scope': scope,
        'key': _entry_key(scope, matcher),
        'label': label,
        'matcher': matcher,
        'added_at': now,
    }


def _movie_matches(entry, context):
    matcher = entry['matcher']
    id_value = matcher.get('id_value')
    if id_value:
        if matcher['id_type'] == 'tmdb' and context.tmdb_id == id_value:
            return True
        if matcher['id_type'] == 'imdb' and context.imdb_id == id_value:
            return True
    if matcher.get('title_norm') and normalize_text(context.title) == matcher['title_norm']:
        entry_year = int(matcher.get('year') or 0)
        return not entry_year or int(context.year or 0) == entry_year
    return False


def _show_matches(entry, context):
    matcher = entry['matcher']
    id_value = matcher.get('id_value')
    if id_value:
        if matcher['id_type'] == 'tvdb' and context.tvdb_id == id_value:
            return True
        if matcher['id_type'] == 'tmdb' and context.tmdb_id == id_value:
            return True
        if matcher['id_type'] == 'imdb' and context.imdb_id == id_value:
            return True
    return (
        matcher.get('show_title_norm') and
        normalize_text(context.show_title or context.title) == matcher['show_title_norm']
    )


def entry_matches_context(entry, context):
    scope = entry['scope']
    if scope == 'movie':
        return not context.is_tv and _movie_matches(entry, context)
    if not context.is_tv or not _show_matches(entry, context):
        return False
    if scope == 'show':
        return True
    if scope == 'season':
        return int(context.season or 0) == int(entry['matcher'].get('season') or 0)
    if scope == 'episode':
        return (
            int(context.season or 0) == int(entry['matcher'].get('season') or 0) and
            int(context.episode or 0) == int(entry['matcher'].get('episode') or 0)
        )
    return False


def entry_covers_entry(parent, child):
    if parent['key'] == child['key']:
        return True
    if parent['scope'] == 'movie' or child['scope'] == 'movie':
        return parent['scope'] == child['scope'] and parent['key'] == child['key']
    if parent['scope'] == 'show' and child['scope'] in ('season', 'episode'):
        return _show_matchers_equal(parent['matcher'], child['matcher'])
    if parent['scope'] == 'season' and child['scope'] == 'episode':
        return (
            _show_matchers_equal(parent['matcher'], child['matcher']) and
            int(parent['matcher'].get('season') or 0) ==
            int(child['matcher'].get('season') or 0)
        )
    return False


def _show_matchers_equal(left, right):
    if left.get('id_value') and right.get('id_value'):
        return (
            left.get('id_type') == right.get('id_type') and
            left.get('id_value') == right.get('id_value')
        )
    return left.get('show_title_norm') == right.get('show_title_norm')


def add_entry(payload, new_entry):
    entries = list(payload.get('entries', []))
    for existing in entries:
        if existing['key'] == new_entry['key']:
            return payload, 'existing', 0
        if entry_covers_entry(existing, new_entry):
            return payload, 'covered', 0

    pruned = [entry for entry in entries if entry_covers_entry(new_entry, entry)]
    updated = [entry for entry in entries if entry not in pruned]
    updated.append(new_entry)
    updated.sort(key=lambda item: (SCOPE_ORDER.index(item['scope']), item['label'].lower()))
    return {
        'schema_version': SCHEMA_VERSION,
        'entries': updated,
    }, 'added', len(pruned)


def remove_entry(payload, key):
    entries = [entry for entry in payload.get('entries', []) if entry['key'] != key]
    return {
        'schema_version': SCHEMA_VERSION,
        'entries': entries,
    }


def runtime_allow_key(context):
    for scope in exact_scopes_for_context(context):
        entry = build_entry(context, scope)
        if entry:
            return entry['key']
    return ''


def exact_scopes_for_context(context):
    if context.is_tv:
        if context.media_type == 'episode' and context.season and context.episode:
            return ['episode', 'season', 'show']
        if context.media_type == 'season' and context.season:
            return ['season', 'show']
        return ['show']
    return ['movie']


def menu_scopes_for_context(context):
    return exact_scopes_for_context(context)


def find_match(entries, context):
    for scope in ('episode', 'season', 'show', 'movie'):
        for entry in entries:
            if entry['scope'] != scope:
                continue
            if entry_matches_context(entry, context):
                return entry
    return None


def empty_payload():
    return {
        'schema_version': SCHEMA_VERSION,
        'entries': [],
    }


def grouped_entries(entries):
    groups = {}
    for scope in SCOPE_ORDER:
        groups[scope] = []
    for entry in entries:
        groups.setdefault(entry['scope'], []).append(entry)
    for scope in groups:
        groups[scope].sort(key=lambda item: item['label'].lower())
    return groups


class WhitelistStore:
    def __init__(self, path):
        self.path = path

    @classmethod
    def from_profile(cls, profile_path):
        return cls(os.path.join(profile_path, 'whitelist.json'))

    def load(self):
        if not os.path.exists(self.path):
            return empty_payload()
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError, TypeError):
            return empty_payload()
        entries = data.get('entries', [])
        return {
            'schema_version': SCHEMA_VERSION,
            'entries': entries,
        }

    def save(self, payload):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

    def list_entries(self):
        return self.load().get('entries', [])

    def find_match(self, context):
        return find_match(self.list_entries(), context)

    def add_context_scope(self, context, scope):
        entry = build_entry(context, scope)
        if not entry:
            return 'invalid', 0, None
        payload = self.load()
        updated, status, pruned = add_entry(payload, entry)
        if status == 'added':
            self.save(updated)
        return status, pruned, entry

    def remove_key(self, key):
        payload = self.load()
        self.save(remove_entry(payload, key))

    def clear(self):
        self.save(empty_payload())
