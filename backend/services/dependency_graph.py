from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import select

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.db.database import get_session
from backend.models.curriculum import DependencyGraph
from backend.services.domain_classifier import classify_domain, get_domain_profile
# DEPRECATED: Legacy provider stub kept for reference
# from backend.services.llm_client import llm_service


class DependencyGraphService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _domain_concept_templates(self, topic: str) -> list[str] | None:
        return None

    def _deterministic_fallback_graph(self, topic: str, level: str, target_concepts: int) -> dict[str, Any]:
        domain_templates = self._domain_concept_templates(topic)
        if domain_templates:
            concepts: list[dict[str, Any]] = []
            previous_name: str | None = None
            for name in domain_templates[: max(3, min(target_concepts, len(domain_templates)) )]:
                prerequisites = [previous_name] if previous_name else []
                concepts.append({
                    "name": name,
                    "prerequisites": prerequisites,
                    "estimated_hours": 2.0 if level == "beginner" else 3.0,
                })
                previous_name = name
            # Return the constructed domain-specific fallback graph
            return {"topic": topic, "level": level, "concepts": concepts}
        else:
            # Generic fallback stays domain-aware even when no keyword template matches.
            domain = classify_domain(topic)
            topic_keywords = re.findall(r'\b[\w\-]+\b', topic.lower())
            if not topic_keywords or topic_keywords[0] in {"learn", "study", "master", "understand"}:
                topic_keywords = re.findall(r'\b[\w\-]+\b', topic.lower())[1:] if len(re.findall(r'\b[\w\-]+\b', topic.lower())) > 1 else ["programming"]
            
            base_topic = " ".join(topic_keywords[:2]) if topic_keywords else "Programming"
            profile = get_domain_profile(domain)
            if domain != "tech_development":
                if level == "beginner":
                    concept_templates = [
                        f"{base_topic} foundations",
                        f"{base_topic} setup",
                        f"Guided {base_topic} practice",
                        f"{base_topic} feedback and reflection",
                    ]
                elif level == "intermediate":
                    concept_templates = [
                        f"{base_topic} planning",
                        f"{base_topic} technique practice",
                        f"{base_topic} troubleshooting",
                        f"Real-world {base_topic} application",
                    ]
                else:
                    concept_templates = [
                        f"Advanced {base_topic} strategy",
                        f"{base_topic} edge cases",
                        f"Independent {base_topic} execution",
                        f"{base_topic} critique and refinement",
                    ]
            elif level == "beginner":
                concept_templates = [
                    f"{base_topic} fundamentals",
                    f"{base_topic} core concepts",
                    f"Practical {base_topic} examples",
                    f"{base_topic} best practices",
                ]
            elif level == "intermediate":
                concept_templates = [
                    f"{base_topic} principles",
                    f"Advanced {base_topic} concepts",
                    f"{base_topic} optimization",
                    f"Real-world {base_topic} projects",
                ]
            else:  # advanced
                concept_templates = [
                    f"Deep dive into {base_topic}",
                    f"Complex {base_topic} patterns",
                    f"Performance and scalability",
                    f"Production-grade {base_topic}",
                ]
            
            concepts: list[dict[str, Any]] = []
            previous_name: str | None = None
            for name in concept_templates[:target_concepts]:
                prerequisites = [previous_name] if previous_name else []
                concepts.append({
                    "name": name,
                    "prerequisites": prerequisites,
                    "estimated_hours": 2.0 if level == "beginner" else 3.0,
                })
                previous_name = name
            return {"topic": topic, "level": level, "concepts": concepts}

    def _graph_prompt(self, topic: str, level: str, target_concepts: int, simplified: bool = False) -> str:
        domain = classify_domain(topic)
        domain_profile = get_domain_profile(domain)
        domain_instruction = f"""
Domain: {domain}
Use this graph style: {domain_profile.get('graph_style', 'Keep the steps concrete and goal-aligned.')}
Use project verbs aligned to: {domain_profile.get('project_action', 'create or practice')}.
Avoid tech-shaped filler like deployments, repositories, or code-first labels unless the topic is actually tech.
"""

        if simplified:
            return f"""
Create a concise learning concept graph for {topic} at {level} level.
{domain_instruction}
Return JSON with keys:
- topic (string)
- concepts (array of objects: {{name: string, prerequisites: string[]}})

Rules:
- Use short, specific lesson titles only.
- Keep the names directly about {topic}.
- Avoid filler language and sentence-like phrasing.
- Return about {target_concepts} concepts if possible.
- Use empty prerequisites for the first concept and simple backward links for the rest.
"""

        return f"""
Create an ordered prerequisite-safe concept graph for learning {topic} at {level} level.
{domain_instruction}
Return JSON with keys:
- topic (string)
- concepts (array of objects: {{name: string, prerequisites: string[]}})

Rules:
- Concepts must be concrete and specific, not generic placeholders.
- Keep each concept name short and lesson-shaped: ideally 2 to 5 words.
- Each concept should be a single lesson-sized step that naturally follows the prior concepts.
- Avoid duplicate concepts or reworded repeats like "overview", "basics", "introduction", "roadmap", "guide", or "fundamentals" unless they are truly distinct.
- Keep roughly {target_concepts} concepts so the curriculum can expand one week at a time.
- Use prerequisites only for earlier concepts in the same array.
- Prefer depth over breadth; if a concept is too generic, replace it with a narrower subskill.
"""

    def _clean_graph(self, topic: str, level: str, concepts: list[dict[str, Any]], target_concepts: int) -> dict[str, Any]:
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()

        for concept_obj in concepts:
            raw_name = str(concept_obj.get("name", "")).strip()
            if not raw_name:
                continue

            normalized = " ".join(raw_name.lower().split())
            if normalized in seen:
                continue

            raw_prerequisites = concept_obj.get("prerequisites", []) or []
            prerequisites = [
                str(prereq).strip()
                for prereq in raw_prerequisites
                if str(prereq).strip() and str(prereq).strip() != raw_name
            ]

            if cleaned:
                valid_prereqs = {item["name"] for item in cleaned}
                prerequisites = [prereq for prereq in prerequisites if prereq in valid_prereqs]
                if not prerequisites:
                    prerequisites = [cleaned[-1]["name"]]

            cleaned.append({"name": raw_name, "prerequisites": prerequisites})
            seen.add(normalized)

            if len(cleaned) >= target_concepts:
                break

        return {"topic": topic, "level": level, "concepts": cleaned}

    def _graph_needs_repair(self, topic: str, concepts: list[dict[str, Any]], target_concepts: int) -> bool:
        if len(concepts) < target_concepts:
            return True

        templated_suffixes = {
            " basics",
            " workflow",
            " examples",
            " patterns",
            " practice",
            " review",
            " next steps",
        }
        templated_hits = 0
        for concept_obj in concepts[: min(len(concepts), 10)]:
            name = str(concept_obj.get("name", "")).lower().strip()
            if any(name.endswith(suffix) for suffix in templated_suffixes):
                templated_hits += 1
        if templated_hits >= 2:
            return True

        generic_terms = {
            "overview",
            "basics",
            "fundamentals",
            "introduction",
            "roadmap",
            "intro",
            "guide",
            "getting started",
            "what is",
            "learn",
            "lesson",
        }
        topic_tokens = {token for token in re.findall(r"[a-z0-9]+", topic.lower()) if len(token) > 2}
        generic_hits = 0

        for concept_obj in concepts[: min(len(concepts), 8)]:
            name = str(concept_obj.get("name", "")).lower()
            if not name:
                continue

            token_hits = sum(1 for token in topic_tokens if token in name)
            if token_hits == 0 and any(term in name for term in generic_terms):
                generic_hits += 1
            elif token_hits == 0 and len(name.split()) <= 3:
                generic_hits += 1

        return generic_hits >= max(2, min(4, len(concepts) // 4))

    def graph_needs_repair(self, topic: str, concepts: list[dict[str, Any]], target_concepts: int) -> bool:
        return self._graph_needs_repair(topic, concepts, target_concepts)

    def _repair_graph(
        self,
        topic: str,
        level: str,
        target_concepts: int,
        previous_graph: dict[str, Any] | None = None,
        provider: str = "nvidia",
    ) -> dict[str, Any]:
        domain = classify_domain(topic)
        domain_profile = get_domain_profile(domain)
        previous_json = previous_graph or {}
        prompt = f"""
You are repairing a learning dependency graph for {topic} at {level} level.
Domain: {domain}
Use this graph style: {domain_profile.get('graph_style', 'Keep the steps concrete and goal-aligned.')}
Return JSON with keys:
- topic (string)
- concepts (array of objects: {{name: string, prerequisites: string[]}})

Rules:
- Use concrete, lesson-sized concepts that are directly about {topic}.
- Keep each concept name short: 2 to 5 words is ideal.
- Do not use sentence-shaped labels like "overview and learning roadmap" or "first simple examples".
- Avoid generic labels such as overview, basics, fundamentals, roadmap, intro, guide, or what is unless combined into a short lesson title.
- Avoid duplicates, near-duplicates, and filler concepts.
- Make the prerequisites strictly point to earlier concepts.
- Produce exactly {target_concepts} concepts if possible.
- The graph must support a progressive week-by-week curriculum.

Existing graph to fix:
{previous_json}
"""
        # DEPRECATED: Legacy stub returns empty for now
        return {"concepts": []}

    def _fallback_graph(self, topic: str, level: str, target_concepts: int, provider: str = "nvidia") -> dict[str, Any]:
        # DEPRECATED: Legacy stub
        return {"topic": topic, "level": level, "concepts": []}

    def _generate_graph_with_provider(self, topic: str, level: str, target_concepts: int, provider: str) -> dict[str, Any]:
        # DEPRECATED: Legacy stub
        return {"concepts": []}

        return {"topic": topic, "level": level, "concepts": []}

    def build_graph(self, topic: str, level: str, duration_weeks: int | None = None) -> dict[str, Any]:
        domain = classify_domain(topic)
        cache_key = f"dependency:{domain}:{topic}:{level}".lower()
        cached = redis_cache.get_json(cache_key)
        target_concepts = max(13, (duration_weeks or 3) * 7)

        if cached and len(cached.get("concepts", [])) >= target_concepts:
            cached_concepts = cached.get("concepts", []) if isinstance(cached, dict) else []
            if isinstance(cached_concepts, list) and not self._graph_needs_repair(topic, cached_concepts, min(target_concepts, len(cached_concepts))):
                return cached

        graph = self._generate_graph_with_provider(topic, level, target_concepts, provider="ollama")
        if not graph.get("concepts"):
            graph = self._generate_graph_with_provider(topic, level, target_concepts, provider="nvidia")
        if graph.get("concepts") and self._graph_needs_repair(topic, graph.get("concepts", []), min(target_concepts, len(graph.get("concepts", [])))):
            try:
                repaired = self._repair_graph(topic, level, target_concepts, previous_graph=graph, provider="nvidia")
                repaired_concepts = repaired.get("concepts", []) if isinstance(repaired, dict) else []
                if isinstance(repaired_concepts, list) and repaired_concepts:
                    graph = self._clean_graph(topic, level, repaired_concepts, min(target_concepts, len(repaired_concepts)))
            except RuntimeError:
                pass
        if not graph.get("concepts"):
            graph = self._deterministic_fallback_graph(topic, level, target_concepts)

        redis_cache.set_json(cache_key, graph, self._settings.dependency_cache_ttl_seconds)

        with get_session() as session:
            expires_at = datetime.utcnow() + timedelta(seconds=self._settings.dependency_cache_ttl_seconds)
            row = session.exec(
                select(DependencyGraph)
                .where(DependencyGraph.topic == topic)
                .where(DependencyGraph.level == level)
            ).first()
            if row:
                row.graph_json = graph
                row.expires_at = expires_at
                session.add(row)
            else:
                session.add(
                    DependencyGraph(topic=topic, level=level, graph_json=graph, expires_at=expires_at)
                )
            session.commit()

        return graph


class SpacedRepetitionExpander:
    """Expand concepts with spaced repetition for better retention."""
    
    @staticmethod
    def expand_concepts_for_retention(
        concepts: list[dict[str, Any]],
        level: str,
        duration_weeks: int,
    ) -> list[dict[str, Any]]:
        """
        Expand concepts to include spaced repetition across weeks.
        
        Example: If level=intermediate and duration_weeks=3:
        - Week 1: concepts 1-3 at intermediate depth ("pattern", "practice")
        - Week 2: revisit 1-2 at advanced depth + new concepts 4-5
        - Week 3: final revisit + new concepts 6-7
        
        This helps retention by spacing out concept review and deepening understanding.
        """
        if not concepts or duration_weeks < 2:
            return concepts
        
        expanded: list[dict[str, Any]] = []
        concepts_per_week = max(3, len(concepts) // max(1, duration_weeks - 1))
        
        # First pass: core concepts
        for i, concept in enumerate(concepts[:concepts_per_week]):
            expanded.append({
                **concept,
                "_phase": "foundational",
                "_week_suggested": 1,
            })
        
        # Second pass: interleaved revisit + new concepts (if duration > 1 week)
        if duration_weeks > 1:
            # Revisit earlier concepts at deeper level
            for concept in concepts[:min(2, concepts_per_week)]:
                revisit = {
                    "name": f"{concept['name']} (advanced)",
                    "prerequisites": [concept["name"]],
                    "_phase": "reinforcement",
                    "_week_suggested": 2,
                }
                expanded.append(revisit)
            
            # Add new concepts
            next_start = concepts_per_week
            next_end = min(next_start + concepts_per_week, len(concepts))
            for concept in concepts[next_start:next_end]:
                expanded.append({
                    **concept,
                    "_phase": "extension",
                    "_week_suggested": 2,
                })
        
        # Third pass: final review + advanced (if duration > 2 weeks)
        if duration_weeks > 2:
            for concept in concepts[:min(1, concepts_per_week)]:
                final_review = {
                    "name": f"{concept['name']} (mastery)",
                    "prerequisites": [concept["name"], f"{concept['name']} (advanced)"] if len(concepts) > 1 else [concept["name"]],
                    "_phase": "mastery",
                    "_week_suggested": 3,
                }
                expanded.append(final_review)
            
            # More new concepts
            next_start = next_end
            next_end = min(next_start + concepts_per_week, len(concepts))
            for concept in concepts[next_start:next_end]:
                expanded.append({
                    **concept,
                    "_phase": "extension",
                    "_week_suggested": 3,
                })
        
        return expanded if expanded else concepts


dependency_graph_service = DependencyGraphService()
