import os
import sys
import unittest


ADDON_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    'service.bouncer',
)
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

from resources.lib.playback import PlaybackContext, finalize_context
from resources.lib.rating import build_tmdb_lookup_plan, normalize_rating, tmdb_cache_key
from resources.lib.whitelist import runtime_allow_key


class PlaybackAndRatingTests(unittest.TestCase):
    def test_movie_context_uses_plain_title_for_tmdb_lookup(self):
        context = finalize_context(PlaybackContext(
            media_type='movie',
            title='Dune: Part Two',
            year=2024,
        ))

        self.assertEqual(context.display_title, 'Dune: Part Two (2024)')
        self.assertEqual(context.lookup_title, 'Dune: Part Two')

        plan = build_tmdb_lookup_plan(context)
        self.assertEqual(plan['strategy'], 'search')
        self.assertEqual(plan['query'], 'Dune: Part Two')
        self.assertEqual(plan['year'], 2024)

    def test_episode_context_uses_show_title_for_tmdb_lookup(self):
        context = finalize_context(PlaybackContext(
            media_type='episode',
            show_title='The Last of Us',
            season=1,
            episode=2,
        ))

        self.assertEqual(context.display_title, 'The Last of Us S01E02')
        self.assertEqual(context.lookup_title, 'The Last of Us')

        plan = build_tmdb_lookup_plan(context)
        self.assertEqual(plan['strategy'], 'search')
        self.assertEqual(plan['query'], 'The Last of Us')
        self.assertTrue(plan['is_tv'])

    def test_tmdb_cache_key_prefers_ids(self):
        context = finalize_context(PlaybackContext(
            media_type='movie',
            title='Alien',
            year=1979,
            tmdb_id='348',
        ))
        self.assertEqual(tmdb_cache_key(context), 'tmdb:movie:348')

    def test_runtime_allow_key_uses_exact_episode_scope(self):
        context = finalize_context(PlaybackContext(
            media_type='episode',
            show_title='Severance',
            season=2,
            episode=3,
            tvdb_id='412345',
        ))
        self.assertEqual(
            runtime_allow_key(context),
            'show:tvdb:412345:season:2:episode:3',
        )

    def test_rating_normalization_handles_movies_and_tv(self):
        self.assertEqual(normalize_rating('PG-13'), 'PG13')
        self.assertEqual(normalize_rating('tv-ma'), 'TVMA')
        self.assertEqual(normalize_rating(''), '')


if __name__ == '__main__':
    unittest.main()
