"""Tests for sky.utils.log_links.

These mirror sky/dashboard/src/utils/externalLinks.test.js so the Python
(server/controller) extractor and the JS (dashboard fallback) extractor stay in
agreement.
"""
import re

from sky.utils import log_links

WANDB_URL = 'https://wandb.ai/test-entity/test-project/runs/abc12345'
WANDB_LINE = ('(wandb-link-test, pid=886) wandb: 🚀 View run b300-smoke-test '
              f'at: {WANDB_URL}')


def _builtin_patterns():
    return {
        label: re.compile(regex)
        for label, regex in log_links.BUILTIN_PATTERNS.items()
    }


class TestExtractLinksFromLines:
    """Port of the extractLinksFromLogs JS test cases."""

    def test_extracts_wandb_run_url_from_prefixed_line(self):
        links = log_links.extract_links_from_lines(
            ['Starting fake training run...', WANDB_LINE], _builtin_patterns())
        assert links == {'W&B Run': WANDB_URL}

    def test_ignores_non_run_wandb_urls(self):
        links = log_links.extract_links_from_lines([
            'wandb: ⭐️ View project at: '
            'https://wandb.ai/test-entity/test-project'
        ], _builtin_patterns())
        assert not links

    def test_strips_ansi_escape_codes_around_url(self):
        ansi_line = f'\x1b[36mwandb:\x1b[0m View run at: {WANDB_URL}\x1b[0m'
        links = log_links.extract_links_from_lines([ansi_line],
                                                   _builtin_patterns())
        assert links == {'W&B Run': WANDB_URL}

    def test_skips_non_string_entries_without_throwing(self):
        links = log_links.extract_links_from_lines(
            [None, 42, {
                'msg': 'metadata'
            }, WANDB_LINE], _builtin_patterns())
        assert links == {'W&B Run': WANDB_URL}

    def test_preserves_existing_matches(self):
        existing = {'W&B Run': 'https://wandb.ai/a/b/runs/first'}
        links = log_links.extract_links_from_lines([WANDB_LINE],
                                                   _builtin_patterns(),
                                                   existing=dict(existing))
        assert links == existing

    def test_no_patterns_returns_empty(self):
        assert not log_links.extract_links_from_lines([WANDB_LINE], {})


class TestExtractCandidateUrls:
    """Worker-side candidate-URL harvesting."""

    def test_harvests_distinct_http_urls_in_order(self):
        urls = log_links.extract_candidate_urls([
            'see https://a.com/x and https://b.com/y',
            'again https://a.com/x',
        ])
        assert urls == ['https://a.com/x', 'https://b.com/y']

    def test_strips_trailing_punctuation_and_ansi(self):
        urls = log_links.extract_candidate_urls(
            [f'\x1b[36mView run at: {WANDB_URL}.\x1b[0m'])
        assert urls == [WANDB_URL]

    def test_ignores_non_http_tokens(self):
        urls = log_links.extract_candidate_urls(
            ['ftp://x.com file:///etc/passwd just-text'])
        assert not urls

    def test_respects_cap(self):
        lines = [f'https://example.com/{i}' for i in range(10)]
        urls = log_links.extract_candidate_urls(lines, cap=3)
        assert len(urls) == 3

    def test_preserves_existing(self):
        urls = log_links.extract_candidate_urls(['https://b.com/y'],
                                                existing=['https://a.com/x'])
        assert urls == ['https://a.com/x', 'https://b.com/y']

    def test_skips_non_string_entries(self):
        urls = log_links.extract_candidate_urls([None, 42, WANDB_LINE])
        assert urls == [WANDB_URL]


class TestMatchLinks:
    """Matching pre-harvested candidate URLs against patterns."""

    def test_first_matching_candidate_wins(self):
        links = log_links.match_links([
            'https://wandb.ai/e/p', WANDB_URL, 'https://wandb.ai/e2/p2/runs/z'
        ], _builtin_patterns())
        assert links == {'W&B Run': WANDB_URL}

    def test_no_patterns_returns_empty(self):
        assert not log_links.match_links([WANDB_URL], {})

    def test_admin_pattern_matches(self):
        patterns = {'MLflow': re.compile(r'^https://mlflow\.example\.com/.+$')}
        links = log_links.match_links(['https://mlflow.example.com/runs/42'],
                                      patterns)
        assert links == {'MLflow': 'https://mlflow.example.com/runs/42'}


class TestCompileAdminPatterns:
    """Compiling admin-configured dashboard.external_links entries."""

    def test_compiles_valid_entries(self):
        compiled = log_links.compile_admin_patterns([{
            'label': 'X',
            'regex': r'^https://x\.com/.+$'
        }])
        assert set(compiled) == {'X'}
        assert compiled['X'].search('https://x.com/abc')

    def test_skips_invalid_regex(self):
        compiled = log_links.compile_admin_patterns([{
            'label': 'Bad',
            'regex': r'('
        }, {
            'label': 'Good',
            'regex': r'^https://good\.com/.+$'
        }])
        assert set(compiled) == {'Good'}

    def test_skips_malformed_entries(self):
        assert not log_links.compile_admin_patterns(
            [None, {}, {
                'label': 'no-regex'
            }, 'str'])
        assert not log_links.compile_admin_patterns(None)


class TestGetPatterns:
    """Combining built-in and admin patterns from config."""

    def test_includes_builtins(self, monkeypatch):
        monkeypatch.setattr(log_links.skypilot_config, 'get_nested',
                            lambda *a, **k: [])
        patterns = log_links.get_patterns()
        assert 'W&B Run' in patterns
        assert patterns['W&B Run'].search(WANDB_URL)

    def test_merges_admin_patterns(self, monkeypatch):
        monkeypatch.setattr(
            log_links.skypilot_config, 'get_nested', lambda *a, **k: [{
                'label': 'MLflow',
                'regex': r'^https://mlflow\.example\.com/.+$'
            }])
        patterns = log_links.get_patterns()
        assert {'W&B Run', 'MLflow'}.issubset(set(patterns))

    def test_admin_overrides_builtin_on_collision(self, monkeypatch):
        monkeypatch.setattr(
            log_links.skypilot_config, 'get_nested', lambda *a, **k: [{
                'label': 'W&B Run',
                'regex': r'^https://override\.example\.com/.+$'
            }])
        patterns = log_links.get_patterns()
        assert patterns['W&B Run'].search('https://override.example.com/x')
        assert not patterns['W&B Run'].search(WANDB_URL)
