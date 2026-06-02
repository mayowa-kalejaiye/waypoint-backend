#!/usr/bin/env python3
"""Debug script to diagnose curriculum generation bottleneck."""

import sys
import os

# Add waypoint to path
sys.path.insert(0, os.path.dirname(__file__))

from backend.services.goal_parser import goal_parser
from backend.services.dependency_graph import dependency_graph_service
from backend.services.content_fetcher import content_fetcher

# Test parameters
GOAL = "Python for data science"
LEVEL = "beginner"

print("=" * 70)
print(f"DIAGNOSING: {GOAL} at {LEVEL} level")
print("=" * 70)

# Step 1: Parse goal
print("\n[STEP 1] GOAL PARSING")
print("-" * 70)
parsed = goal_parser.parse_goal(GOAL, LEVEL)
print(f"Topic: {parsed['topic']}")
print(f"Duration weeks: {parsed.get('duration_weeks', 3)}")
print(f"Focus areas: {parsed.get('focus_areas', [])}")

# Step 2: Generate dependency graph
print("\n[STEP 2] DEPENDENCY GRAPH")
print("-" * 70)
topic = parsed["topic"]
duration_weeks = int(parsed.get("duration_weeks", 3))
try:
    graph = dependency_graph_service.build_graph(topic, LEVEL, duration_weeks)
    all_concepts = graph.get("concepts", [])
    print(f"Total concepts generated: {len(all_concepts)}")
    for idx, concept in enumerate(all_concepts, 1):
        if isinstance(concept, dict):
            name = concept.get("name", str(concept))
            prereqs = concept.get("prerequisites", [])
            print(f"  {idx}. {name} (prereqs: {prereqs})")
        else:
            print(f"  {idx}. {concept}")
except Exception as e:
    print(f"ERROR building graph: {e}")
    import traceback
    traceback.print_exc()
    all_concepts = []

# Step 3: Fetch candidates
print("\n[STEP 3] CONTENT FETCHING")
print("-" * 70)
initial_count = min(7, 7 * 1)  # CONCEPTS_PER_WEEK=7, INITIAL_WEEKS=1
initial_concepts = all_concepts[:initial_count] if len(all_concepts) > initial_count else all_concepts

concept_names = [c["name"] if isinstance(c, dict) else str(c) for c in initial_concepts]
print(f"Initial concepts to fetch for: {concept_names}")
print()

try:
    candidates, warning = content_fetcher.fetch_candidates(
        concept_names,
        level=LEVEL,
        context=" ".join(concept_names[:3])
    )
    print(f"Total candidates fetched: {len(candidates)}")
    if warning:
        print(f"Warning: {warning}")
    
    # Show breakdown by source
    sources = {}
    for c in candidates:
        source = c.get("source", "youtube")
        sources[source] = sources.get(source, 0) + 1
    print(f"Candidates by source: {sources}")
    
    # Show sample candidates
    if candidates:
        print("\nSample candidates (first 5):")
        for idx, c in enumerate(candidates[:5], 1):
            source = c.get("source", "youtube")
            title = c.get("title", "N/A")[:60]
            youtube_id = c.get("youtube_id", "N/A")
            print(f"  {idx}. [{source}] {title} (id: {youtube_id[:20]}...)")
    else:
        print("NO CANDIDATES RETURNED!")
        
except Exception as e:
    print(f"ERROR fetching candidates: {e}")
    import traceback
    traceback.print_exc()
    candidates = []

# Step 4: Pre-rank for first concept
if candidates and concept_names:
    print(f"\n[STEP 4] PRE-RANKING FOR FIRST CONCEPT: {concept_names[0]}")
    print("-" * 70)
    from backend.routers.curriculum import _pre_rank_candidates_for_concept, MAX_SCORABLE_CANDIDATES_PER_CONCEPT
    
    shortlisted = _pre_rank_candidates_for_concept(
        concept_names[0],
        candidates,
        MAX_SCORABLE_CANDIDATES_PER_CONCEPT
    )
    print(f"Shortlisted candidates for '{concept_names[0]}': {len(shortlisted)}")
    if shortlisted:
        print("Top 3:")
        for idx, c in enumerate(shortlisted[:3], 1):
            title = c.get("title", "N/A")[:60]
            score_components = c.get("score_components", {})
            print(f"  {idx}. {title}")
            print(f"      (initial score: {score_components})")

print("\n" + "=" * 70)
print("DIAGNOSIS COMPLETE")
print("=" * 70)
