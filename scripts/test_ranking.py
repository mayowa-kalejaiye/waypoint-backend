import logging
from backend.routers.curriculum import _pre_rank_candidates_for_concept
from backend.services.scoring_engine import scoring_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

concept = "pandas dataframes"

candidates = [
    {
        'youtube_id': 'yt1',
        'title': 'Pandas DataFrames Tutorial - Basics',
        'description': 'An introduction to pandas DataFrame operations and basics.',
        'channel': 'DataChannel',
        'duration_seconds': 600,
        'view_count': 120000,
        'published_at': '2023-01-01T00:00:00Z',
        'source': 'youtube',
        'transcript_cached': True,
    },
    {
        'id': 'doc1',
        'title': 'Pandas User Guide',
        'description': 'Official documentation for pandas DataFrame API and examples.',
        'duration_seconds': 0,
        'source': 'docs',
    },
    {
        'paper_id': 'arxiv1',
        'title': 'Efficient DataFrame Operations for Large Datasets',
        'description': 'Advanced techniques and benchmarks.',
        'source': 'arxiv',
        'duration_seconds': 0,
    },
    {
        'repo_id': 'gh1',
        'title': 'awesome-pandas-examples',
        'description': 'A repo with many pandas code examples and notebooks.',
        'source': 'github',
        'duration_seconds': 0,
        'view_count': 5000,
    },
    {
        'youtube_id': 'yt2',
        'title': 'Data munging with numpy',
        'description': 'Covers numpy arrays, not pandas.',
        'channel': 'OtherChannel',
        'duration_seconds': 400,
        'view_count': 20000,
        'published_at': '2020-06-01T00:00:00Z',
        'source': 'youtube',
    }
]

print('\n--- Pre-ranking ---')
shortlisted = _pre_rank_candidates_for_concept(concept, candidates, limit=4)
for i, s in enumerate(shortlisted, 1):
    print(i, s.get('source'), s.get('title'))

print('\n--- Scoring ---')
for s in shortlisted:
    analysis = {
        'relevance_overlap': 0.5 if s.get('source')=='youtube' else 0.2,
        'transcript_word_count': 800 if s.get('source')=='youtube' else 0,
        'transcript_available': True if s.get('source')=='youtube' else False,
        'reading_level': 4.0 if s.get('source') in ('youtube','docs') else 10.0,
        'content_density': 0.6,
        'has_code_examples': s.get('source') in ('github','docs'),
    }
    result = scoring_engine.score_video(s, analysis, concept, level='beginner')
    print(s.get('source'), s.get('title'), '->', result)
