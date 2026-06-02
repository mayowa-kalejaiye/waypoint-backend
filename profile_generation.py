#!/usr/bin/env python3
"""
Profiling script for curriculum generation to identify bottlenecks.
"""
import sys
import logging
import os

# Configure logging to see [PERF] markers
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, '.')

# Suppress Redis warnings
logging.getLogger('redis').setLevel(logging.ERROR)

from backend.models.curriculum import GenerateCurriculumRequest
from backend.routers.curriculum import _generate_curriculum_payload

def profile_generation(goal: str, level: str = "Beginner", hours_per_day: float = 1.0):
    """Profile curriculum generation."""
    print(f"\n{'='*70}")
    print(f"Profiling: {goal}")
    print(f"{'='*70}\n")
    
    request = GenerateCurriculumRequest(
        goal=goal,
        level=level,
        hours_per_day=hours_per_day,
        session_id=f"profile-{goal.replace(' ', '-')}"
    )
    
    try:
        curriculum_payload, topic, cache_key, videos, session_id = _generate_curriculum_payload(request)
        print(f"\n[SUCCESS] Generation complete!")
        print(f"  Topic: {topic}")
        print(f"  Videos selected: {len(videos)}")
        print(f"  Concepts: {len(curriculum_payload.get('weeks', [{}])[0].get('days', []))}")
    except Exception as e:
        print(f"\n[FAILED] Generation failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # Profile a few test cases
    test_cases = [
        "Learn Python Basics",
        "Learn JavaScript",
    ]
    
    for goal in test_cases:
        profile_generation(goal)

