import os
import sys
import tempfile
import unittest


ADDON_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'service.bouncer',
)
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

from resources.lib.playback import PlaybackContext, finalize_context
from resources.lib.whitelist import (
    WhitelistStore,
    add_entry,
    build_entry,
    empty_payload,
    find_match,
)


class WhitelistTests(unittest.TestCase):
    def episode_context(self):
        return finalize_context(PlaybackContext(
            media_type='episode',
            show_title='Breaking Bad',
            season=2,
            episode=3,
            tvdb_id='81189',
        ))

    def test_show_entry_prunes_child_entries(self):
        context = self.episode_context()
        payload = empty_payload()
        payload, status, pruned = add_entry(payload, build_entry(context, 'episode', now='2026-04-30T00:00:00Z'))
        self.assertEqual(status, 'added')
        self.assertEqual(pruned, 0)

        payload, status, pruned = add_entry(payload, build_entry(context, 'show', now='2026-04-30T00:01:00Z'))
        self.assertEqual(status, 'added')
        self.assertEqual(pruned, 1)
        self.assertEqual([entry['scope'] for entry in payload['entries']], ['show'])

    def test_child_add_is_noop_when_parent_already_exists(self):
        context = self.episode_context()
        payload = empty_payload()
        payload, _, _ = add_entry(payload, build_entry(context, 'show', now='2026-04-30T00:00:00Z'))
        payload, status, pruned = add_entry(payload, build_entry(context, 'episode', now='2026-04-30T00:01:00Z'))
        self.assertEqual(status, 'covered')
        self.assertEqual(pruned, 0)
        self.assertEqual(len(payload['entries']), 1)

    def test_find_match_respects_season_scope(self):
        context = self.episode_context()
        season_entry = build_entry(context, 'season', now='2026-04-30T00:00:00Z')
        self.assertEqual(find_match([season_entry], context)['scope'], 'season')

        other_episode = finalize_context(PlaybackContext(
            media_type='episode',
            show_title='Breaking Bad',
            season=3,
            episode=1,
            tvdb_id='81189',
        ))
        self.assertIsNone(find_match([season_entry], other_episode))

    def test_movie_entry_matches_by_title_year_without_ids(self):
        context = finalize_context(PlaybackContext(
            media_type='movie',
            title='Coco',
            year=2017,
        ))
        entry = build_entry(context, 'movie', now='2026-04-30T00:00:00Z')
        self.assertEqual(find_match([entry], context)['scope'], 'movie')

    def test_store_persists_and_removes_entries(self):
        context = self.episode_context()
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WhitelistStore.from_profile(tmpdir)
            status, _, entry = store.add_context_scope(context, 'episode')
            self.assertEqual(status, 'added')
            self.assertEqual(len(store.list_entries()), 1)

            store.remove_key(entry['key'])
            self.assertEqual(store.list_entries(), [])


if __name__ == '__main__':
    unittest.main()
