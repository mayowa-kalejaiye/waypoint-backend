import logging
from backend.services.scoring_engine import scoring_engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [%(name)s] %(message)s')

concept = "python data analysis"

# Mock a video and a playlist candidate
candidates = [
    {
        'youtube_id': 'single_video',
        'title': 'Learn Python for Data Analysis - 20 min intro',
        'description': 'Quick intro to pandas and numpy.',
        'channel': 'TechChannel',
        'duration_seconds': 1200,
        'view_count': 50000,
        'published_at': '2023-06-01T00:00:00Z',
        'source': 'youtube',
        'is_playlist': False,
    },
    {
        'youtube_id': 'playlist_comprehensive',
        'title': 'Python for Data Analysis - Complete Course',
        'description': 'Full tutorial series covering pandas, numpy, matplotlib, statistics.',
        'channel': 'DataMastery',
        'duration_seconds': 36000,  # 10 hours = 100 videos @ 6min avg, or playlist estimate
        'item_count': 60,
        'view_count': 0,
        'published_at': '2022-01-01T00:00:00Z',
        'source': 'youtube',
        'is_playlist': True,
    }
]

print("=== Scoring: Single Video vs Playlist ===\n")

for c in candidates:
    analysis = {
        'relevance_overlap': 0.6,
        'transcript_word_count': 800 if not c.get('is_playlist') else 0,
        'transcript_available': not c.get('is_playlist'),
        'reading_level': 5.0,
        'content_density': 0.5,
        'has_code_examples': True,
    }
    
    result = scoring_engine.score_video(c, analysis, concept, level='beginner')
    
    print(f"Type: {'PLAYLIST' if c.get('is_playlist') else 'VIDEO'}")
    print(f"Title: {c['title']}")
    print(f"Duration: {c['duration_seconds'] / 3600:.1f} hours" if c['duration_seconds'] > 3600 else f"Duration: {c['duration_seconds'] / 60:.0f} min")
    print(f"Score: {result['score']:.4f}")
    print(f"Reason: {result['why_this_video']}")
    print(f"Depth: {result['components']['depth']:.4f}")
    print()
