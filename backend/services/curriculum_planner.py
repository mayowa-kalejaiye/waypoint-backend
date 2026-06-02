from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Callable

import requests

from backend.cache.redis_client import redis_cache
from backend.config import get_settings
from backend.models.curriculum import GenerateCurriculumRequest
from backend.services.diversity_validator import validate_diversity_fit
from backend.services.domain_classifier import classify_domain, get_domain_profile
from backend.services.dependency_graph import dependency_graph_service
from backend.services.groq_client import groq_service
from backend.services.path_assembler import path_assembler
from backend.services.scoring_engine import scoring_engine
from backend.services.youtube_fetcher import YouTubeQuotaExceededError, youtube_fetcher

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _extract_youtube_id_from_url(url: str) -> str:
    url_value = str(url or "").strip()
    if not url_value:
        return ""
    match = re.search(r"(?:v=|youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,})", url_value)
    if match:
        return match.group(1)
    tail_match = re.search(r"([A-Za-z0-9_-]{6,})$", url_value)
    return tail_match.group(1) if tail_match else ""


def _make_youtube_watch_url(value: str) -> str:
    cleaned_value = str(value or "").strip()
    if not cleaned_value:
        return ""
    if cleaned_value.startswith("http://") or cleaned_value.startswith("https://"):
        return cleaned_value
    return f"https://www.youtube.com/watch?v={cleaned_value}"


def _query_terms(text: str) -> set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "your",
        "you",
        "that",
        "this",
        "learn",
        "learning",
        "video",
        "videos",
        "what",
        "how",
        "best",
        "guide",
        "tutorial",
        "full",
        "course",
        "lecture",
        "deep",
        "dive",
    }
    tokens = {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}
    return {token for token in tokens if token not in stop_words}


def _is_strictly_relevant(candidate_text: str, node_text: str, query_text: str) -> bool:
    query_terms = _query_terms(query_text)
    node_terms = _query_terms(node_text)
    candidate_terms = _query_terms(candidate_text)
    signal_terms = (node_terms | query_terms) - {"lesson"}
    if not signal_terms:
        return True
    return bool(signal_terms & candidate_terms)


class CurriculumPlanner:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _format_duration_per_day(self, hours_per_day: float) -> str:
        minutes = max(1, int(round(hours_per_day * 60)))
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours} hour" if hours == 1 else f"{hours} hours"
        return f"{minutes} minutes"

    def _master_prompt(self, prompt: str, level: str, duration_per_day: str, domain: str | None = None) -> str:
        domain_name = domain or classify_domain(prompt)
        domain_profile = get_domain_profile(domain_name)
        template = """
[SYSTEM ROLE]
You are an expert curriculum designer and dependency graph architect. You specialize in breaking down any learning topic into a directed acyclic graph of prerequisites. You output only valid JSON unless specified otherwise.

[USER INPUT]
Topic: "{{prompt}}"
Learner Level: {{level}} (options: beginner, intermediate, advanced)
Daily available time: {{duration_per_day}} minutes/hours

[TASK]
Generate a complete curriculum package as a single JSON object with the following structure. Do not skip fields. Do not add explanations outside JSON.

{
  "dependency_graph": {
    "nodes": [
      {
        "id": "string (unique, kebab-case)",
        "name": "string (human readable)",
        "description": "string (what learner will master)",
        "estimated_days": "number (based on {{duration_per_day}})",
        "depends_on": ["array of node ids"],
        "difficulty": "beginner|intermediate|advanced (relative to overall level)"
      }
    ],
    "edges_note": "depends_on arrays define the DAG. No cycles."
  },
  "youtube_strategy": {
    "per_node": [
      {
        "node_id": "string",
        "search_queries": [
          "string (3-5 search queries tailored to this node and level. For advanced: include specific concepts, edge cases. For beginner: include 'tutorial', 'explained', 'for beginners')"
        ],
        "scoring_rubric": {
          "primary_criteria": [
            {
              "criterion": "string (e.g., 'relevance_to_subtopic')",
              "weight": "number 0-1",
              "ideal_threshold": "string or number"
            }
          ],
          "penalties": [
            {
              "condition": "string (e.g., 'video is longer than 2x {{duration_per_day}}')",
              "penalty": "number (subtracted from total score)"
            }
          ],
          "boosters": [
            {
              "condition": "string (e.g., 'includes downloadable code or transcript')",
              "boost": "number"
            }
          ]
        },
        "min_score_to_keep": "number 0-100"
      }
    ]
  },
  "resource_strategy": {
    "per_node": [
      {
        "node_id": "string",
        "resource_types": ["github", "official_docs", "interactive_tutorial", "blog_post", "podcast", "cheatsheet"],
        "search_terms": ["string (what to search GitHub/docs for, e.g., 'react hooks example repo', 'postgres indexing tutorial')"],
        "authority_boost": ["string domains or GitHub orgs to prioritize, e.g., 'mdn.io', 'vercel', 'facebook'"],
        "max_resources_per_type": "number"
      }
    ]
  },
  "projects": {
    "daily_breakdown": [
      {
        "day": "number",
        "node_ids_covered": ["array of node ids from dependency_graph"],
        "project_title": "string",
        "duration_minutes": "number (<= {{duration_per_day}})",
        "description": "string (clear, actionable, level-appropriate)",
        "deliverable": "string (what they produce)",
        "hints": ["array of strings (subtle nudges, not full solutions)"],
                "success_criteria": ["array of strings (how they know they're done)"],
                "external_links": [
                    {"label": "string", "url": "https://example.com", "type": "repo|docs|reference|tool"}
                ]
      }
    ],
    "final_project": {
      "title": "string",
      "integrates_nodes": ["array of node ids"],
      "description": "string",
      "estimated_total_hours": "number",
            "rubric": "string (how to self-evaluate)",
            "external_links": [
                {"label": "string", "url": "https://example.com", "type": "repo|docs|reference|tool"}
            ]
    }
  },
  "adaptive_rules": {
    "if_learner_struggles": {
      "action": "string (e.g., 'add remedial node', 'suggest prerequisite video from earlier node')",
      "trigger": "string (e.g., 'fails success criteria twice')"
    },
    "if_learner_accelerates": {
      "action": "string (e.g., 'skip next beginner node', 'add advanced challenge')",
      "trigger": "string"
    }
  }
}

[SPECIAL INSTRUCTIONS FOR LEVEL {{level}}]
- If beginner: Max 6-8 nodes total. Each node's estimated_days = 1-2 days. Projects must be fully guided. Avoid jargon. YouTube queries should include "for beginners", "step by step", "no prior knowledge".
- If intermediate: 10-14 nodes. Some parallel branches allowed. Projects are open-ended with constraints. YouTube queries should include "best practices", "deep dive", "real world example".
- If advanced: 12-18 nodes. Expect learner to fill gaps. Projects are specification-only but domain-appropriate (for tech, implementation constraints; for non-tech, outcome constraints). YouTube queries should target in-depth patterns and edge cases. Scoring rubric penalizes introductory content heavily.

[CRITICAL RULES FOR ALL LEVELS]
1. The dependency graph must be a true DAG (no cycles). If topic A needs B and B needs A, split into smaller concepts.
2. For every node, the learner should be able to complete it within (estimated_days * {{duration_per_day}}). Be realistic.
3. YouTube scoring rubric must be machine-executable. Avoid "good explanation" and use observable signals like demonstrations, visual aids, process clarity, and learner outcomes.
4. Projects should build on each other. Day N's project can reuse Day N-1's output.
5. If topic is non-technical (e.g., "public speaking", "gardening"), replace GitHub with appropriate sources: Pinterest, WikiHow, academic papers, practice communities.
6. If a project benefits from external context, include explicit `external_links` entries with the exact URL and a short label. Do not strip or replace those URLs during normalization.
7. This is an educational platform centered on learner development, so prioritize depth, usefulness, and care over short clips.
8. For YouTube, prefer substantive, in-depth videos (for example, 20+ minutes) when available, but allow high-quality shorter tutorials (10-20 minutes) when they better match the learner's level and the concept. Avoid forcing very long-form content when shorter, high-quality resources are more appropriate.
9. Make the dependency graph tree-like and sequential. One parent node may branch into multiple child nodes, but each child must preserve continuity with the parent sequence.
10. If a branch exists, include a clear parent reference in the node fields and explain the continuity between parent and child.

[DOMAIN INSTRUCTION]
The user's topic has been classified as {{domain_name}}.
Use domain-appropriate verbs, resources, dependency graphs, and success criteria.
Graph style: {{graph_style}}
Project verb preference: {{project_action}}
Success criteria examples: {{success_criteria}}
For non-tech domains, do not default to GitHub, repos, deployments, build pipelines, or code-first examples.

[OUTPUT FORMAT]
Return ONLY valid JSON. No markdown code fences. No explanatory text. Start with { and end with }.
"""
        return (
            template.replace("{{prompt}}", prompt)
            .replace("{{level}}", level)
            .replace("{{duration_per_day}}", duration_per_day)
            .replace("{{domain_name}}", domain_name)
            .replace("{{graph_style}}", str(domain_profile.get("graph_style", "Use a clear prerequisite chain.")))
            .replace("{{project_action}}", str(domain_profile.get("project_action", "create or practice")))
            .replace("{{success_criteria}}", ", ".join(str(item) for item in domain_profile.get("success_criteria", [])))
        )

    def _expected_node_range(self, level: str) -> tuple[int, int]:
        level_name = level.lower().strip()
        if level_name == "beginner":
            # Accept 5 nodes as a valid beginner curriculum (5-8)
            return 5, 8
        if level_name == "advanced":
            return 12, 18
        return 10, 14

    def _normalize_id(self, value: str, fallback_index: int) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or f"node-{fallback_index + 1}"

    def _normalize_node_fields(self, raw_nodes: list, default_level: str) -> list[dict[str, Any]]:
        """Convert LLM node field variants to the canonical internal schema.

        Ensures each node has: id, name, description, duration_days (int), dependencies (list), difficulty
        Skips nodes missing id/name/description.
        """
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(raw_nodes or []):
            if not isinstance(raw, dict):
                continue

            # id & name
            raw_id = str(raw.get("id") or raw.get("node_id") or raw.get("name") or f"node-{idx+1}")
            node_id = self._normalize_id(raw_id, idx)
            # avoid duplicates
            suffix = 2
            base_id = node_id
            while node_id in seen_ids:
                node_id = f"{base_id}-{suffix}"
                suffix += 1
            seen_ids.add(node_id)

            name = str(raw.get("name") or raw.get("title") or raw.get("label") or "").strip()
            # description aliases
            description = raw.get("description") or raw.get("desc") or raw.get("summary") or ""

            # duration aliases
            dur = raw.get("duration_days") if raw.get("duration_days") is not None else raw.get("estimated_days")
            if dur is None:
                dur = raw.get("days") or raw.get("duration") or 1
            try:
                duration_days = max(1, int(round(float(dur))))
            except Exception:
                duration_days = 1

            # dependencies aliases
            deps = raw.get("dependencies") or raw.get("depends_on") or raw.get("prereqs") or []
            if not isinstance(deps, list):
                deps = [str(deps)] if deps else []
            deps = [self._normalize_id(str(d), i) for i, d in enumerate(deps) if d]

            difficulty = str(raw.get("difficulty") or default_level or "beginner").lower().strip()
            if difficulty not in {"beginner", "intermediate", "advanced"}:
                difficulty = default_level or "beginner"

            if not node_id or not name or not description:
                logger.warning("Skipping invalid node during normalization: %s", raw)
                continue

            normalized.append(
                {
                    "id": node_id,
                    "name": name,
                    "description": str(description).strip(),
                    "duration_days": duration_days,
                    "dependencies": deps,
                    "difficulty": difficulty,
                }
            )

        return normalized

    def _validate_plan(self, plan: dict[str, Any], level: str) -> None:
        lower, upper = self._expected_node_range(level)
        dependency_graph = plan.get("dependency_graph")
        if not isinstance(dependency_graph, dict):
            raise ValueError("missing dependency_graph object")

        nodes = dependency_graph.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("dependency_graph.nodes must be a non-empty array")
        if len(nodes) < lower or len(nodes) > upper:
            raise ValueError(f"dependency_graph.nodes must contain between {lower} and {upper} nodes for {level}")

        youtube_strategy = plan.get("youtube_strategy", {})
        youtube_nodes = youtube_strategy.get("per_node", []) if isinstance(youtube_strategy, dict) else []
        if not isinstance(youtube_nodes, list) or len(youtube_nodes) < len(nodes):
            raise ValueError("youtube_strategy.per_node must cover each dependency node")

        resource_strategy = plan.get("resource_strategy", {})
        resource_nodes = resource_strategy.get("per_node", []) if isinstance(resource_strategy, dict) else []
        if not isinstance(resource_nodes, list) or len(resource_nodes) < len(nodes):
            raise ValueError("resource_strategy.per_node must cover each dependency node")

        projects = plan.get("projects", {})
        if not isinstance(projects, dict):
            raise ValueError("projects must be an object")
        daily_breakdown = projects.get("daily_breakdown", [])
        if not isinstance(daily_breakdown, list) or not daily_breakdown:
            raise ValueError("projects.daily_breakdown must be a non-empty array")
        if not isinstance(projects.get("final_project", {}), dict):
            raise ValueError("projects.final_project must be an object")

    def _repair_plan(self, plan: dict[str, Any], request: GenerateCurriculumRequest) -> dict[str, Any]:
        # Determine level-appropriate minimum/maximum nodes. Use a forgiving minimum
        # to accept compact curricula produced by the LLM.
        lower, upper = self._expected_node_range(request.level)
        # Allow smaller minima for beginner/intermediate levels to avoid rejecting valid outputs
        min_nodes_map = {"beginner": 3, "intermediate": 4, "advanced": 5}
        lower = min_nodes_map.get(request.level.lower().strip(), lower)
        dependency_graph = plan.get("dependency_graph") if isinstance(plan.get("dependency_graph"), dict) else {}
        nodes = dependency_graph.get("nodes", []) if isinstance(dependency_graph, dict) else []

        normalized_nodes = self._normalize_node_fields(nodes[:upper], request.level)

        if len(normalized_nodes) < lower:
            raise ValueError(f"LLM returned only {len(normalized_nodes)} usable nodes after normalization; need at least {lower}")

        normalized = dict(plan)
        normalized["dependency_graph"] = {
            "nodes": normalized_nodes,
            "edges_note": "depends_on arrays define the DAG. No cycles.",
        }
        normalized["youtube_strategy"] = self._normalize_strategy_block(plan.get("youtube_strategy", {}), normalized_nodes, request)
        normalized["resource_strategy"] = self._normalize_resource_block(plan.get("resource_strategy", {}), normalized_nodes, request)
        normalized["projects"] = self._normalize_projects_block(plan.get("projects", {}), normalized_nodes, request)
        normalized["adaptive_rules"] = self._normalize_adaptive_rules(plan.get("adaptive_rules", {}), request)
        return normalized

    def _normalize_strategy_block(self, block: Any, nodes: list[dict[str, Any]], request: GenerateCurriculumRequest) -> dict[str, Any]:
        domain_profile = get_domain_profile(classify_domain(request.goal))
        domain = classify_domain(request.goal)
        is_tech = domain == "tech_development"
        per_node = block.get("per_node", []) if isinstance(block, dict) else []
        per_node_map = {
            str(item.get("node_id", "")).strip(): item
            for item in per_node
            if isinstance(item, dict)
        }

        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            current = per_node_map.get(node["id"])
            if not isinstance(current, dict):
                current = {}
            queries = [str(item).strip() for item in current.get("search_queries", []) or [] if str(item).strip()]
            if not queries:
                queries = [
                    f"{node['name']} {request.level} {verb}" for verb in domain_profile.get("search_verbs", ["tutorial", "guide", "example"])
                ]
            queries = list(dict.fromkeys(queries))[:5]

            rubric = current.get("scoring_rubric", {}) if isinstance(current, dict) else {}
            if not isinstance(rubric, dict):
                rubric = {}
            rubric.setdefault(
                "primary_criteria",
                [
                    {"criterion": "relevance_to_subtopic", "weight": 0.45, "ideal_threshold": 0.7},
                    {"criterion": "content_density", "weight": 0.25, "ideal_threshold": "detailed demonstration or explanation"},
                    {"criterion": "engagement_quality", "weight": 0.15, "ideal_threshold": 0.02},
                    {"criterion": "freshness", "weight": 0.15, "ideal_threshold": 365},
                ],
            )
            if is_tech:
                default_boosters = [
                    {"condition": "includes code walkthrough, transcript, or exercise", "boost": 8},
                    {"condition": "description mentions project, implementation, or hands-on", "boost": 6},
                ]
            else:
                default_boosters = [
                    {"condition": "includes transcript, guided steps, or worked demonstration", "boost": 8},
                    {"condition": "description mentions practice, checklist, reflection, or example", "boost": 6},
                ]
            rubric.setdefault(
                "penalties",
                [
                    {"condition": f"video is longer than 2x {request.hours_per_day} hours", "penalty": 12},
                    {"condition": "title contains beginner or intro fluff when node is advanced", "penalty": 20},
                ],
            )
            rubric.setdefault("boosters", default_boosters)

            min_score = current.get("min_score_to_keep", 40)
            try:
                min_score_value = float(min_score)
            except Exception:
                min_score_value = 40.0

            preferred_youtube_id = None
            preferred_youtube_url = None
            preferred_youtube_title = None
            preferred_youtube_channel = None
            preferred_youtube_query = None
            if isinstance(current, dict):
                preferred_youtube_id = current.get("preferred_youtube_id") or current.get("preferred_youtube_id")
                preferred_youtube_url = current.get("preferred_youtube_url") or current.get("youtube_url") or current.get("video_url")
                preferred_youtube_title = current.get("preferred_youtube_title")
                preferred_youtube_channel = current.get("preferred_youtube_channel")
                preferred_youtube_query = current.get("preferred_youtube_query") or current.get("preferred_youtube_query")

            if preferred_youtube_url and not preferred_youtube_id:
                preferred_youtube_id = _extract_youtube_id_from_url(str(preferred_youtube_url))
            if preferred_youtube_id and not preferred_youtube_url:
                preferred_youtube_url = _make_youtube_watch_url(preferred_youtube_id)

            normalized_nodes.append(
                {
                    "node_id": node["id"],
                    "search_queries": queries,
                    "scoring_rubric": rubric,
                    "min_score_to_keep": min_score_value,
                    "preferred_youtube_id": preferred_youtube_id,
                    "preferred_youtube_url": preferred_youtube_url,
                    "preferred_youtube_title": preferred_youtube_title,
                    "preferred_youtube_channel": preferred_youtube_channel,
                    "preferred_youtube_query": preferred_youtube_query,
                }
            )

        return {"per_node": normalized_nodes}

    def _normalize_resource_block(self, block: Any, nodes: list[dict[str, Any]], request: GenerateCurriculumRequest) -> dict[str, Any]:
        domain_profile = get_domain_profile(classify_domain(request.goal))
        per_node = block.get("per_node", []) if isinstance(block, dict) else []
        per_node_map = {
            str(item.get("node_id", "")).strip(): item
            for item in per_node
            if isinstance(item, dict)
        }

        normalized_nodes: list[dict[str, Any]] = []
        for node in nodes:
            current = per_node_map.get(node["id"])
            if not isinstance(current, dict):
                current = {}
            resource_types = [str(item).strip() for item in current.get("resource_types", []) or [] if str(item).strip()]
            if not resource_types:
                resource_types = list(domain_profile.get("resource_types", ["official_docs"]))
            search_terms = [str(item).strip() for item in current.get("search_terms", []) or [] if str(item).strip()]
            if not search_terms:
                search_verbs = [str(item).strip() for item in domain_profile.get("search_verbs", ["overview", "analysis", "summary"]) if str(item).strip()]
                if classify_domain(request.goal) == "tech_development":
                    search_terms = [node["name"], f"{node['name']} examples", f"{node['name']} tutorial"]
                else:
                    search_terms = [node["name"]] + [f"{node['name']} {verb}" for verb in search_verbs[:3]]
            authority_boost = [str(item).strip() for item in current.get("authority_boost", []) or [] if str(item).strip()]
            if not authority_boost:
                authority_boost = ["wikihow.com", "britannica.com", "youtube.com"] if classify_domain(request.goal) != "tech_development" else ["mdn.io", "vercel", "github.com"]
            max_resources = current.get("max_resources_per_type", 3)
            try:
                max_resources_value = max(1, min(5, int(max_resources)))
            except Exception:
                max_resources_value = 3

            normalized_nodes.append(
                {
                    "node_id": node["id"],
                    "resource_types": resource_types,
                    "search_terms": search_terms,
                    "authority_boost": authority_boost,
                    "max_resources_per_type": max_resources_value,
                }
            )

        return {"per_node": normalized_nodes}

    def _normalize_projects_block(self, block: Any, nodes: list[dict[str, Any]], request: GenerateCurriculumRequest) -> dict[str, Any]:
        domain_profile = get_domain_profile(classify_domain(request.goal))
        projects = block if isinstance(block, dict) else {}
        daily_breakdown = projects.get("daily_breakdown", []) if isinstance(projects, dict) else []
        normalized_days: list[dict[str, Any]] = []
        node_ids = [node["id"] for node in nodes]
        duration_minutes_target = max(15, int(round(request.hours_per_day * 60)))

        if isinstance(daily_breakdown, list) and daily_breakdown:
            for index, day in enumerate(daily_breakdown, start=1):
                if not isinstance(day, dict):
                    continue
                node_refs = [str(item).strip() for item in day.get("node_ids_covered", []) or [] if str(item).strip()]
                if not node_refs and node_ids:
                    node_refs = [node_ids[min(index - 1, len(node_ids) - 1)]]
                duration_minutes = day.get("duration_minutes", duration_minutes_target)
                try:
                    duration_minutes_value = min(int(round(float(duration_minutes))), duration_minutes_target)
                except Exception:
                    duration_minutes_value = duration_minutes_target

                normalized_days.append(
                    {
                        "day": int(day.get("day", index)),
                        "node_ids_covered": node_refs,
                        "project_title": str(day.get("project_title", f"{domain_profile.get('project_verb', 'Create').title()} {index}")).strip() or f"{domain_profile.get('project_verb', 'Create').title()} {index}",
                        "duration_minutes": max(15, duration_minutes_value),
                        "description": str(day.get("description", "")).strip() or f"Apply the day's concepts in a concrete {domain_profile.get('project_action', 'practice')}.",
                        "deliverable": str(day.get("deliverable", "")).strip() or "A completed artifact or documented outcome.",
                        "hints": [str(item).strip() for item in day.get("hints", []) or [] if str(item).strip()],
                        "success_criteria": [str(item).strip() for item in day.get("success_criteria", []) or [] if str(item).strip()] or list(domain_profile.get("success_criteria", [])),
                    }
                )

            # Sanitize project suggestions for non-technical domains: remove GitHub links and rewrite techy project titles
            is_tech_domain = classify_domain(request.goal) == "tech_development"
            tech_terms = ["data", "analysis", "repo", "github", "api", "docker", "kubernetes", "build", "implementation", "code", "program"]
            for d in normalized_days:
                title = str(d.get("project_title", "")).lower()
                if not is_tech_domain and any(t in title for t in tech_terms):
                    day_num = int(d.get("day", 1))
                    d["project_title"] = f"{domain_profile.get('project_verb', 'Create').title()} Day {day_num}"
                    d["description"] = f"Apply the day's concepts with a short, domain-appropriate {domain_profile.get('project_action', 'practice')}."
                    d["deliverable"] = "A short written reflection or annotated notes with references."
                    d["hints"] = d.get("hints", []) or ["Keep the scope narrow.", "Focus on primary sources or authoritative summaries."]
                for link_field in ("external_links", "resource_links", "reference_links", "github_url", "repo_url"):
                    if link_field in d and d[link_field] is None:
                        d[link_field] = [] if link_field.endswith("links") else ""
        else:
            for index, node in enumerate(nodes, start=1):
                normalized_days.append(
                    {
                        "day": index,
                        "node_ids_covered": [node["id"]],
                        "project_title": f"{domain_profile.get('project_verb', 'Create').title()} with {node['name']}",
                        "duration_minutes": duration_minutes_target,
                        "description": f"Practice {node['name']} in a short, level-appropriate {domain_profile.get('project_action', 'practice')}.",
                        "deliverable": "A completed artifact or documented outcome.",
                        "hints": ["Start with the smallest version first.", "Keep the scope narrow and validate early."],
                        "success_criteria": list(domain_profile.get("success_criteria", ["The project is completed as specified.", "The output matches the requested constraints."])) ,
                    }
                )

        final_project = projects.get("final_project", {}) if isinstance(projects, dict) else {}
        if not isinstance(final_project, dict) or not final_project:
            final_project = {
                "title": f"Capstone: {request.goal}",
                "integrates_nodes": node_ids,
                "description": f"Combine the curriculum into a single {request.level} capstone.",
                "estimated_total_hours": max(1.0, round(len(nodes) * request.hours_per_day * 0.75, 2)),
                "rubric": "Score yourself on correctness, completeness, clarity, and whether the solution demonstrates the target skills without external help.",
            }
        elif isinstance(final_project, dict):
            for link_field in ("external_links", "resource_links", "reference_links"):
                if link_field in final_project and final_project[link_field] is None:
                    final_project[link_field] = []

        return {"daily_breakdown": normalized_days, "final_project": final_project}

    def _normalize_adaptive_rules(self, block: Any, request: GenerateCurriculumRequest) -> dict[str, Any]:
        if not isinstance(block, dict):
            block = {}
        struggles = block.get("if_learner_struggles", {}) if isinstance(block, dict) else {}
        accelerates = block.get("if_learner_accelerates", {}) if isinstance(block, dict) else {}
        if not isinstance(struggles, dict):
            struggles = {}
        if not isinstance(accelerates, dict):
            accelerates = {}
        struggles.setdefault("action", "add remedial node")
        struggles.setdefault("trigger", "fails the success criteria twice")
        accelerates.setdefault("action", "skip the next easiest node and add an advanced challenge")
        accelerates.setdefault("trigger", "passes success criteria early and wants more depth")
        return {"if_learner_struggles": struggles, "if_learner_accelerates": accelerates}

    def _build_learner_value_bundle(
        self,
        request: GenerateCurriculumRequest,
        nodes: list[dict[str, Any]],
        assembled: dict[str, Any],
    ) -> dict[str, Any]:
        domain_profile = get_domain_profile(classify_domain(request.goal))
        topic = request.goal.strip()
        node_names = [str(node.get("name", "")).strip() for node in nodes if str(node.get("name", "")).strip()]
        first_focus = node_names[0] if node_names else topic
        final_focus = node_names[-1] if node_names else topic

        week_themes = [str(week.get("theme", "")).strip() for week in assembled.get("weeks", []) if str(week.get("theme", "")).strip()]
        milestones: list[str] = []
        if week_themes:
            milestones.append(f"Week 1: get fluent with {week_themes[0]}")
        if len(week_themes) > 1:
            milestones.append("Week 2: turn the ideas into applied practice")
        milestones.append(f"End state: connect {first_focus} through {final_focus} into a clear working model")

        transfer_skills = [
            "problem decomposition",
            "self-directed practice",
            "clear communication",
            "debugging and recovery",
        ]

        outcomes = [
            f"Understand the core ideas behind {topic} in a sequenced way.",
            f"Make steady progress without having to guess the next step.",
            f"Finish with something you can explain and reuse outside the tutorial.",
        ]
        roi_summary = (
            f"This path gives you a structured way to build confidence in {topic}, reduce dead-end learning, "
            f"and turn each day into a visible win."
        )

        return {
            "roi_summary": roi_summary,
            "outcomes": outcomes,
            "transfer_skills": transfer_skills,
            "milestones": milestones,
            "daily_commitment": f"{request.hours_per_day:.1f}h/day for {len(nodes)} nodes",
            "practice_style": str(domain_profile.get("project_action", "practice and apply")),
            "coach_note": f"Stay focused on the smallest useful outcome each day, then build upward.",
        }

    def _prompt_with_retry(self, request: GenerateCurriculumRequest, max_attempts: int = 1, domain: str | None = None) -> dict[str, Any]:
        """Call Groq to generate curriculum. Hard failure on error (no fallbacks)."""
        domain_name = domain or classify_domain(request.goal)
        daily_minutes = int(round(request.hours_per_day * 60))
        
        logger.info("Calling Groq for curriculum: goal=%s level=%s domain=%s", 
                    request.goal, request.level, domain_name)
        
        # Call Groq (will raise on failure - no fallbacks)
        try:
            plan = groq_service.call_groq(
                goal=request.goal,
                level=request.level,
                daily_minutes=daily_minutes,
                domain=domain_name
            )
            
            # Repair/normalize response first (will fill missing strategy/resource blocks)
            repaired = self._repair_plan(plan, request)

            # Validate quality on the repaired/normalized plan
            if not groq_service.validate_curriculum_quality(repaired, request.level):
                raise ValueError("Curriculum failed quality validation")

            return repaired
            
        except Exception as exc:
            logger.exception("Groq curriculum generation failed: %s", exc)
            raise RuntimeError(f"Unable to generate curriculum: {str(exc)}") from exc

    def _score_match(self, text: str, terms: list[str]) -> float:
        if not terms:
            return 0.0
        haystack = text.lower()
        hits = 0
        for term in terms:
            token = term.lower().strip()
            if token and token in haystack:
                hits += 1
        return hits / max(len(terms), 1)

    def _matches_condition(self, condition: str, candidate: dict[str, Any], node: dict[str, Any], duration_minutes: int) -> bool:
        text = f"{candidate.get('title', '')} {candidate.get('description', '')} {candidate.get('channel', '')}".lower()
        condition_lower = condition.lower()

        if "longer than 2x" in condition_lower:
            return int(candidate.get("duration_seconds", 0) or 0) > duration_minutes * 120
        if "intro" in condition_lower or "beginner" in condition_lower:
            return any(term in text for term in ["intro", "beginner", "basics", "overview"])
        if "captions" in condition_lower or "transcript" in condition_lower:
            return bool(candidate.get("transcript_available")) or any(term in text for term in ["transcript", "captions"])
        if "code" in condition_lower or "exercise" in condition_lower or "hands-on" in condition_lower:
            return any(term in text for term in ["code", "exercise", "hands-on", "walkthrough", "project"])
        if "project" in condition_lower or "implementation" in condition_lower:
            return any(term in text for term in ["project", "implementation", "build", "walkthrough"])
        if "fresh" in condition_lower or "recent" in condition_lower:
            published = str(candidate.get("published_at", ""))
            if not published:
                return False
            try:
                age_days = max(0, (datetime.utcnow() - datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)).days)
            except Exception:
                age_days = 9999
            return age_days < 365

        node_terms = [node["name"], node.get("description", "")]
        return any(term.lower() in text for term in node_terms if term)

    def _criterion_score(self, criterion: str, candidate: dict[str, Any], node: dict[str, Any], query: str, duration_minutes: int) -> float:
        text = f"{candidate.get('title', '')} {candidate.get('description', '')} {candidate.get('channel', '')}".lower()
        criterion_lower = criterion.lower().strip()
        query_terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) > 2]
        node_terms = [term for term in re.findall(r"[a-z0-9]+", f"{node['name']} {node.get('description', '')}".lower()) if len(term) > 2]
        overlap = self._score_match(text, list(dict.fromkeys(query_terms + node_terms)))

        if criterion_lower in {"relevance_to_subtopic", "relevance"}:
            return overlap
        if criterion_lower in {"content_density", "density"}:
            signal = 0.0
            if any(term in text for term in ["code", "exercise", "walkthrough", "project", "implementation"]):
                signal += 0.6
            if len(str(candidate.get("description", ""))) > 150:
                signal += 0.2
            candidate_duration_minutes = int(candidate.get("duration_seconds", 0) or 0) / 60.0
            target_duration_minutes = max(20.0, min(float(duration_minutes), 40.0))
            if candidate_duration_minutes >= target_duration_minutes * 0.75:
                signal += 0.2
            return min(signal, 1.0)
        if criterion_lower in {"engagement_quality", "engagement"}:
            views = float(candidate.get("view_count", 0) or 0)
            likes = float(candidate.get("like_count", 0) or 0)
            ratio = likes / max(views, 1.0)
            return min(1.0, ratio * 20.0 + min(views / 100000.0, 0.5))
        if criterion_lower in {"freshness", "recency"}:
            published = str(candidate.get("published_at", ""))
            if not published:
                return 0.4
            try:
                published_at = datetime.fromisoformat(published.replace("Z", "+00:00")).replace(tzinfo=None)
                age_days = max(0, (datetime.utcnow() - published_at).days)
            except Exception:
                age_days = 9999
            return max(0.0, 1.0 - min(age_days, 730) / 730.0)
        if criterion_lower in {"duration_alignment", "length", "ideal_length"}:
            duration_seconds = int(candidate.get("duration_seconds", 0) or 0)
            if duration_seconds <= 0:
                return 0.55
            duration = duration_seconds / 60.0
            target = max(20.0, min(float(duration_minutes), 40.0))
            delta = abs(duration - target)
            return max(0.0, 1.0 - min(delta / max(target, 1.0), 1.0))
        if criterion_lower in {"advanced_depth", "level_alignment"}:
            if any(term in text for term in ["advanced", "deep dive", "architecture", "optimization", "edge case", "production"]):
                return 1.0
            if any(term in text for term in ["beginner", "intro", "overview", "basics"]):
                return 0.1
            return 0.6
        return overlap

    def _score_candidate(
        self,
        candidate: dict[str, Any],
        node: dict[str, Any],
        strategy: dict[str, Any],
        query: str,
        duration_minutes: int,
        daily_budget_minutes: int,
        domain: str,
    ) -> dict[str, Any]:
        rubric = strategy.get("scoring_rubric", {}) if isinstance(strategy, dict) else {}
        concept = f"{node['name']} {query}".strip()
        analysis = {
            "relevance_overlap": self._score_match(
                f"{candidate.get('title', '')} {candidate.get('description', '')}",
                [token for token in re.findall(r"[a-z0-9]+", concept.lower()) if len(token) > 2],
            ),
            "transcript_word_count": len(str(candidate.get("description", "")).split()) * 4,
            "transcript_available": bool(candidate.get("transcript_available")),
            "content_density": len(str(candidate.get("description", ""))),
            "reading_level": 8.0 if node.get("difficulty") == "advanced" else 5.0,
            "has_code_examples": any(token in str(candidate.get("description", "")).lower() for token in (["code", "walkthrough", "github"] if domain == "tech_development" else ["demonstration", "example", "practice", "guided"])),
        }
        score_payload = scoring_engine.score_video(
            candidate,
            analysis,
            concept=node["name"],
            level=str(node.get("difficulty", strategy.get("level", "beginner"))),
            rubric=rubric,
            daily_budget_minutes=float(daily_budget_minutes),
            domain=domain,
        )
        final_score = float(score_payload.get("final_score", 0.0))
        # Lightweight boost for candidates that include a transcript — helps surface substantive content
        try:
            if bool(candidate.get("transcript_available")):
                final_score = min(100.0, final_score + 8.0)
        except Exception:
            pass
        return {
            **candidate,
            "score": round(final_score / 100.0, 4),
            "final_score": int(round(final_score)),
            "why_this_video": score_payload.get("why_this_video", ""),
            "score_components": {
                "node_id": node["id"],
                "query": query,
                "min_score_to_keep": strategy.get("min_score_to_keep", 72),
                "breakdown": score_payload.get("breakdown", []),
                "recommended_action": score_payload.get("recommended_action"),
                "duration_advice": score_payload.get("duration_advice"),
            },
            "duration_advice": score_payload.get("duration_advice"),
            "recommended_action": score_payload.get("recommended_action"),
        }

    def _search_github(self, search_term: str, authority_boost: list[str], max_resources: int, domain: str | None = None) -> list[dict[str, Any]]:
        if (domain or classify_domain(search_term)) != "tech_development" and not any(token in search_term.lower() for token in ["repo", "github", "code", "api", "programming"]):
            return []
        token = self._settings.github_api_token
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        org_filters = [item for item in authority_boost if item and "." not in item and "/" not in item]
        queries = [search_term]
        if org_filters:
            queries = [f"{search_term} org:{org}" for org in org_filters[:2]]

        results: dict[str, dict[str, Any]] = {}
        for query in queries:
            try:
                response = requests.get(
                    "https://api.github.com/search/repositories",
                    params={"q": f"{query} stars:>10 is:public", "sort": "stars", "order": "desc", "per_page": max(5, max_resources)},
                    headers=headers,
                    timeout=self._settings.youtube_request_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("GitHub search failed for %s: %s", query, exc)
                continue

            for repo in payload.get("items", [])[:max_resources]:
                repo_id = str(repo.get("id") or "").strip()
                if not repo_id or repo_id in results:
                    continue
                # Quick relevance check: ensure the repo name or description mentions the search term (avoid generic github.com links)
                title = str(repo.get("name", "") or "").lower()
                desc = str(repo.get("description", "") or "").lower()
                st_lower = str(search_term or "").lower()
                token_match = False
                for token in [t for t in re.findall(r"[a-z0-9]+", st_lower) if len(token) > 2]:
                    if token in title or token in desc:
                        token_match = True
                        break
                if not token_match:
                    # skip repos that don't contain the search term in name/description
                    continue
                results[repo_id] = {
                    "repo_id": repo.get("full_name", repo_id),
                    "youtube_id": f"github:{repo_id}",
                    "title": repo.get("name", ""),
                    "description": (repo.get("description") or "")[:500],
                    "channel": repo.get("owner", {}).get("login", "GitHub"),
                    "duration_seconds": 0,
                    "view_count": repo.get("stargazers_count", 0),
                    "like_count": repo.get("forks_count", 0),
                    "published_at": repo.get("created_at", "2024-01-01T00:00:00Z"),
                    "thumbnail": "",
                    "source": "github",
                    "url": repo.get("html_url", ""),
                }

        return list(results.values())[:max_resources]

    def _search_docs(self, search_term: str, authority_boost: list[str], level: str, max_resources: int, domain: str | None = None) -> list[dict[str, Any]]:
        docs: dict[str, dict[str, Any]] = {}
        domain_name = domain or classify_domain(search_term)
        poetry_request = any(term in search_term.lower() for term in ("poetry", "poem", "poems", "poet", "sonnet", "haiku", "verse", "literary analysis"))

        curated_non_tech: dict[str, list[tuple[str, str, str]]] = {
            "creative_arts": [
                ("https://www.wikihow.com/", "WikiHow", "Practical how-to guides"),
                ("https://www.almanac.com/", "The Old Farmer's Almanac", "Gardening and seasonal guidance"),
                ("https://www.masterclass.com/articles", "MasterClass Articles", "Creative technique explainers"),
            ],
            "business_professional": [
                ("https://hbr.org/", "Harvard Business Review", "Leadership and communication"),
                ("https://www.ted.com/topics/public+speaking", "TED Talks", "Presentation examples and guidance"),
                ("https://www.toastmasters.org/", "Toastmasters", "Practice resources for speaking"),
            ],
            "health_wellness": [
                ("https://www.mayoclinic.org/", "Mayo Clinic", "Trusted health advice"),
                ("https://www.nhs.uk/", "NHS", "Trusted health guidance"),
                ("https://www.mindful.org/", "Mindful.org", "Meditation and mindfulness practice"),
            ],
            "academic_learning": [
                ("https://www.khanacademy.org/", "Khan Academy", "Foundational academic instruction"),
                ("https://www.britannica.com/", "Britannica", "Academic reference and overview"),
                ("https://plato.stanford.edu/", "Stanford Encyclopedia of Philosophy", "Scholarly reference"),
            ],
            "life_skills": [
                ("https://www.wikihow.com/", "WikiHow", "Step-by-step practical guidance"),
                ("https://www.consumerreports.org/", "Consumer Reports", "Consumer and maintenance advice"),
                ("https://www.askthebuilder.com/", "Ask the Builder", "Home repair and maintenance"),
            ],
            "humanities": [
                ("https://www.metmuseum.org/toah/", "Heilbrunn Timeline of Art History", "Art history and context"),
                ("https://www.britannica.com/", "Britannica", "General humanities reference"),
            ],
        }

        interview_request = any(term in search_term.lower() for term in ("interview", "resume", "career", "behavioral", "job"))
        if domain_name == "business_professional" and interview_request:
            curated_non_tech["business_professional"] = [
                ("https://www.indeed.com/career-advice/interviewing", "Indeed Career Guide", "Interview practice and answer frameworks"),
                ("https://www.themuse.com/advice/interview-questions-and-answers", "The Muse", "Common interview questions and answers"),
                ("https://biginterview.com/interview-advice/behavioral-interview-questions/", "Big Interview", "Behavioral interview strategy"),
                ("https://www.careeronestop.org/JobSearch/interviews/interview-sample-questions.aspx", "CareerOneStop", "Interview prep and sample questions"),
                ("https://hbr.org/", "Harvard Business Review", "Leadership and communication"),
            ]

        if poetry_request and domain_name == "humanities":
            curated_non_tech["humanities"].insert(1, ("https://www.poetryfoundation.org/", "Poetry Foundation", "Poetry analysis and examples"))

        if domain_name in curated_non_tech:
            for url, title, description in curated_non_tech[domain_name]:
                docs[url] = {
                    "doc_id": url,
                    "youtube_id": f"docs:{re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')}",
                    "title": title,
                    "description": f"{description} for {search_term}",
                    "channel": title,
                    "duration_seconds": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "published_at": "2024-01-01T00:00:00Z",
                    "thumbnail": "",
                    "source": "docs",
                    "url": url,
                }
            return list(docs.values())[:max_resources]

        canonical_docs = {
            "react": ("https://react.dev/", "React Docs", "Meta"),
            "next": ("https://nextjs.org/docs", "Next.js Docs", "Vercel"),
            "next.js": ("https://nextjs.org/docs", "Next.js Docs", "Vercel"),
            "javascript": ("https://developer.mozilla.org/en-US/docs/Web/JavaScript", "MDN JavaScript", "Mozilla"),
            "typescript": ("https://www.typescriptlang.org/docs/", "TypeScript Docs", "Microsoft"),
            "python": ("https://docs.python.org/3/", "Python Docs", "Python.org"),
            "fastapi": ("https://fastapi.tiangolo.com/", "FastAPI Docs", "tiangolo"),
            "django": ("https://docs.djangoproject.com/", "Django Docs", "Django Software Foundation"),
            "node": ("https://nodejs.org/en/docs", "Node.js Docs", "OpenJS Foundation"),
            "postgres": ("https://www.postgresql.org/docs/", "PostgreSQL Docs", "PostgreSQL Global Development Group"),
            "sql": ("https://www.postgresql.org/docs/", "PostgreSQL Docs", "PostgreSQL Global Development Group"),
            "docker": ("https://docs.docker.com/", "Docker Docs", "Docker"),
            "kubernetes": ("https://kubernetes.io/docs/", "Kubernetes Docs", "CNCF"),
            "aws": ("https://docs.aws.amazon.com/", "AWS Docs", "Amazon"),
            "go": ("https://go.dev/doc/", "Go Docs", "Google"),
            "rust": ("https://doc.rust-lang.org/book/", "Rust Book", "Rust Project Developers"),
            "html": ("https://developer.mozilla.org/en-US/docs/Web/HTML", "MDN HTML", "Mozilla"),
            "css": ("https://developer.mozilla.org/en-US/docs/Web/CSS", "MDN CSS", "Mozilla"),
            "vue": ("https://vuejs.org/guide/", "Vue Docs", "Vue"),
            "angular": ("https://angular.dev/", "Angular Docs", "Google"),
            "github": ("https://docs.github.com/", "GitHub Docs", "GitHub"),
        }

        search_lower = search_term.lower()
        for keyword, (url, title, channel) in canonical_docs.items():
            if keyword in search_lower:
                docs[url] = {
                    "doc_id": url,
                    "youtube_id": f"docs:{re.sub(r'[^a-z0-9]+', '-', keyword).strip('-')}",
                    "title": title,
                    "description": f"Official documentation for {search_term}",
                    "channel": channel,
                    "duration_seconds": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "published_at": "2024-01-01T00:00:00Z",
                    "thumbnail": "",
                    "source": "docs",
                    "url": url,
                }

        if not docs:
            docs["https://developer.mozilla.org/en-US/docs/"] = {
                "doc_id": "https://developer.mozilla.org/en-US/docs/",
                "youtube_id": "docs:mdn_docs",
                "title": "MDN Web Docs",
                "description": f"General documentation reference for {search_term}",
                "channel": "Mozilla",
                "duration_seconds": 0,
                "view_count": 0,
                "like_count": 0,
                "published_at": "2024-01-01T00:00:00Z",
                "thumbnail": "",
                "source": "docs",
                "url": "https://developer.mozilla.org/en-US/docs/",
            }

        for domain in authority_boost:
            if "." not in domain and "/" not in domain:
                continue
            normalized_url = domain if domain.startswith("http") else f"https://{domain.strip('/')}/"
            # Only include authority URLs that appear related to the search term to avoid generic site roots
            st_lower = str(search_term or "").lower()
            title_candidate = domain.lower()
            token_match = False
            for token in [t for t in re.findall(r"[a-z0-9]+", st_lower) if len(t) > 2]:
                if token in normalized_url.lower() or token in title_candidate:
                    token_match = True
                    break
            if not token_match:
                # skip unrelated authority URL
                continue
            if normalized_url not in docs:
                docs[normalized_url] = {
                    "doc_id": normalized_url,
                    "youtube_id": f"docs:{re.sub(r'[^a-z0-9]+', '-', domain.lower()).strip('-')}",
                    "title": domain.replace("https://", "").replace("http://", "").strip("/"),
                    "description": f"Authoritative resource for {search_term}",
                    "channel": domain,
                    "duration_seconds": 0,
                    "view_count": 0,
                    "like_count": 0,
                    "published_at": "2024-01-01T00:00:00Z",
                    "thumbnail": "",
                    "source": "docs",
                    "url": normalized_url,
                }

        return list(docs.values())[:max_resources]

    def _execute_strategy(
        self,
        plan: dict[str, Any],
        request: GenerateCurriculumRequest,
        duration_minutes: int,
        domain: str,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any], list[dict[str, Any]], str | None]:
        nodes = plan["dependency_graph"]["nodes"]
        youtube_entries = {str(item["node_id"]): item for item in plan.get("youtube_strategy", {}).get("per_node", [])}
        resource_entries = {str(item["node_id"]): item for item in plan.get("resource_strategy", {}).get("per_node", [])}
        long_form_floor_seconds = max(3600, int(self._settings.long_video_threshold_seconds or 3600))

        scored_by_concept: dict[str, list[dict[str, Any]]] = {}
        resources_by_node: dict[str, dict[str, list[dict[str, Any]]]] = {}
        search_results_by_node: dict[str, list[dict[str, Any]]] = {}
        quota_warning: str | None = None
        selected_content: list[dict[str, Any]] = []
        quota_exhausted = False

        def _is_long_form_video(video: dict[str, Any]) -> bool:
            return int(video.get("duration_seconds", 0) or 0) >= long_form_floor_seconds

        def _strong_relevance_score(video: dict[str, Any], node: dict[str, Any], query: str) -> float:
            video_text = f"{video.get('title', '')} {video.get('description', '')} {video.get('channel', '')}".lower()
            node_text = f"{node.get('name', '')} {node.get('description', '')} {query}".lower()
            candidate_terms = [
                term
                for term in re.findall(r"[a-z0-9]+", node_text)
                if len(term) > 3 and term not in {"tutorial", "course", "guide", "learn", "build", "create", "make", "step", "stepbystep", "beginner", "advanced", "intermediate", "example", "examples", "full", "video", "videos", "website"}
            ]
            if not candidate_terms:
                candidate_terms = [term for term in re.findall(r"[a-z0-9]+", node.get("name", "").lower()) if len(term) > 3]
            if not candidate_terms:
                return 0.0
            unique_terms = list(dict.fromkeys(candidate_terms))
            matches = sum(1 for term in unique_terms if term in video_text)
            return matches / max(len(unique_terms), 1)

        def _looks_related(video: dict[str, Any], node: dict[str, Any], query: str) -> bool:
            return _strong_relevance_score(video, node, query) >= 0.25

        def _preferred_video_candidate(node: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any] | None:
            preferred_url = str(strategy.get("preferred_youtube_url") or "").strip()
            preferred_id = str(strategy.get("preferred_youtube_id") or "").strip() or _extract_youtube_id_from_url(preferred_url)
            if preferred_id and not preferred_url:
                preferred_url = _make_youtube_watch_url(preferred_id)
            if not preferred_url and not preferred_id:
                return None

            preferred_title = str(strategy.get("preferred_youtube_title") or node.get("name") or "").strip() or node["name"]
            preferred_channel = str(strategy.get("preferred_youtube_channel") or "").strip()
            preferred_duration = strategy.get("preferred_youtube_duration_seconds")
            try:
                preferred_duration_seconds = int(preferred_duration) if preferred_duration is not None else 0
            except Exception:
                preferred_duration_seconds = 0

            return {
                "youtube_id": preferred_id or preferred_url,
                "url": preferred_url,
                "title": preferred_title,
                "description": str(strategy.get("preferred_youtube_reason") or strategy.get("evidence") or node.get("description") or "").strip(),
                "channel": preferred_channel or "Groq",
                "duration_seconds": preferred_duration_seconds,
                "view_count": int(strategy.get("preferred_youtube_view_count") or 0),
                "like_count": int(strategy.get("preferred_youtube_like_count") or 0),
                "published_at": str(strategy.get("preferred_youtube_published_at") or "").strip(),
                "thumbnail": f"https://img.youtube.com/vi/{preferred_id}/hqdefault.jpg" if preferred_id else "",
                "source": "youtube",
                "transcript_available": bool(strategy.get("transcript_available", False)),
            }

        def _accept_preferred_candidate(node: dict[str, Any], strategy: dict[str, Any], candidate: dict[str, Any]) -> bool:
            candidate_text = f"{candidate.get('title', '')} {candidate.get('channel', '')} {candidate.get('url', '')}"
            node_text = f"{node.get('name', '')} {node.get('description', '')}"
            query_text = str(strategy.get("preferred_youtube_query") or " ").strip()
            if not _is_strictly_relevant(candidate_text, node_text, query_text):
                logger.warning(
                    "[YT] Dropping Groq candidate for node '%s' because it does not match the lesson topic: %s",
                    node["name"],
                    candidate.get("title") or candidate.get("url") or candidate.get("youtube_id"),
                )
                return False
            return True

        for node in nodes:
            node_id = node["id"]
            strategy = youtube_entries.get(node_id, {})
            search_queries = strategy.get("search_queries", []) if isinstance(strategy, dict) else []
            node_candidates: dict[str, dict[str, Any]] = {}
            raw_scored_videos: dict[str, dict[str, Any]] = {}
            min_score_keep = float(strategy.get("min_score_to_keep", 40) or 40)

            logger.info("[YT] Node '%s' search queries: %s", node["name"], search_queries[:5])

            # Honor Groq-provided preferred YouTube suggestions when present
            preferred_id = strategy.get("preferred_youtube_id") if isinstance(strategy, dict) else None
            preferred_url = strategy.get("preferred_youtube_url") if isinstance(strategy, dict) else None
            preferred_query = strategy.get("preferred_youtube_query") if isinstance(strategy, dict) else None
            if preferred_url or preferred_id:
                preferred_candidate = _preferred_video_candidate(node, strategy)
                if preferred_candidate and _accept_preferred_candidate(node, strategy, preferred_candidate):
                    preferred_query_value = preferred_query or preferred_id or preferred_url or node["name"]
                    scored_video = self._score_candidate(
                        preferred_candidate,
                        node,
                        strategy,
                        str(preferred_query_value),
                        max(0.0, float(preferred_candidate.get("duration_seconds", 0) or 0) / 60.0),
                        duration_minutes,
                        domain,
                    )
                    try:
                        scored_video["final_score"] = int(min(100, int(scored_video.get("final_score", 0)) + 12))
                        scored_video["score"] = round(float(scored_video["final_score"]) / 100.0, 4)
                    except Exception:
                        pass
                    preferred_key = str(preferred_candidate.get("youtube_id") or preferred_candidate.get("url") or "").strip()
                    if preferred_key:
                        raw_scored_videos[preferred_key] = dict(scored_video)
                        node_candidates[preferred_key] = dict(scored_video)
                    logger.info("[YT] Using Groq-recommended video for node '%s': %s", node["name"], preferred_candidate.get("url") or preferred_candidate.get("youtube_id"))
            else:
                search_roots: list[str] = []
                if preferred_query:
                    search_roots.append(str(preferred_query).strip())
                preferred_title = str(strategy.get("preferred_youtube_title") or "").strip()
                if preferred_title:
                    search_roots.append(preferred_title)
                for query_text in search_queries[:3]:
                    query_value = str(query_text).strip()
                    if query_value and query_value not in search_roots:
                        search_roots.append(query_value)

                for query_text in search_roots[:5]:
                    if quota_exhausted:
                        break
                    try:
                        videos = youtube_fetcher.search_query(query_text, max_results=5)
                    except YouTubeQuotaExceededError as exc:
                        quota_warning = str(exc) or "YouTube API quota exceeded. Using cached results only."
                        quota_exhausted = True
                        logger.warning("YouTube quota exhausted while searching for %s: %s", query_text, exc)
                        break
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("YouTube search failed for %s: %s", query_text, exc)
                        continue

                    logger.info("[YT] Search for node '%s' returned %d raw results for query '%s'", node["name"], len(videos), query_text)

                    for video in videos:
                        youtube_id = str(video.get("youtube_id") or "").strip()
                        if not youtube_id:
                            continue
                        candidate_duration_minutes = max(0.0, float(video.get("duration_seconds", 0) or 0) / 60.0)
                        scored_video = self._score_candidate(video, node, strategy, query_text, candidate_duration_minutes, duration_minutes, domain)
                        score = float(scored_video.get("final_score", 0.0))
                        raw_scored_videos[youtube_id] = dict(scored_video)
                        if not _is_long_form_video(video) or not _looks_related(video, node, query_text):
                            continue
                        if score < min_score_keep:
                            continue
                        current = dict(scored_video)
                        current["duration_advice"] = scored_video.get("duration_advice")
                        existing = node_candidates.get(youtube_id)
                        if not existing or float(existing.get("final_score", 0.0)) < score:
                            node_candidates[youtube_id] = current

                    if len(node_candidates) >= 3 or quota_exhausted:
                        break

            if not node_candidates and not (preferred_url or preferred_id or preferred_query or search_queries):
                logger.info("[YT] No Groq video recommendation for node '%s'; proceeding with resource fallbacks only.", node["name"])

            if not node_candidates and raw_scored_videos:
                logger.warning("[YT] No videos met score floor for node '%s' (min=%.1f); using top raw results", node["name"], min_score_keep)
                ranked_raw = sorted(raw_scored_videos.values(), key=lambda item: float(item.get("final_score", item.get("score", 0.0))), reverse=True)
                for video in ranked_raw[:3]:
                    youtube_id = str(video.get("youtube_id") or "").strip()
                    if youtube_id:
                        node_candidates[youtube_id] = dict(video)

            ranked_videos = sorted(node_candidates.values(), key=lambda item: float(item.get("final_score", item.get("score", 0.0))), reverse=True)
            ranked_videos = ranked_videos[:1]
            search_results_by_node[node_id] = ranked_videos
            logger.info("[YT] Final video for node '%s': %s", node["name"], [video.get("title") for video in ranked_videos])

            resource_plan = resource_entries.get(node_id, {}) if isinstance(resource_entries.get(node_id, {}), dict) else {}
            resource_candidates: dict[str, dict[str, Any]] = {}
            search_terms = resource_plan.get("search_terms", []) if isinstance(resource_plan, dict) else []
            authority_boost = resource_plan.get("authority_boost", []) if isinstance(resource_plan, dict) else []
            max_resources_per_type = int(resource_plan.get("max_resources_per_type", 3) or 3)

            resource_types = [str(item).strip() for item in resource_plan.get("resource_types", []) or [] if str(item).strip()]
            for search_term in search_terms[:3]:
                if "github" in resource_types:
                    for repo in self._search_github(search_term, authority_boost, max_resources_per_type, domain):
                        repo_id = str(repo.get("youtube_id") or repo.get("repo_id") or "").strip()
                        if not repo_id:
                            continue
                        repo_score = 60.0 + self._score_match(f"{repo.get('title', '')} {repo.get('description', '')}", [search_term]) * 40.0
                        repo["score"] = round(repo_score / 100.0, 4)
                        repo["final_score"] = int(round(repo_score))
                        repo["why_this_video"] = f"GitHub match for {search_term}"
                        repo["score_components"] = {"node_id": node_id, "resource_type": "github", "search_term": search_term}
                        repo["duration_advice"] = scoring_engine.get_duration_advice(0, duration_minutes, request.level)
                        resource_candidates[repo_id] = repo

                if "official_docs" in resource_types:
                    for doc in self._search_docs(search_term, authority_boost, request.level, max_resources_per_type, domain):
                        doc_id = str(doc.get("youtube_id") or doc.get("doc_id") or doc.get("url") or "").strip()
                        if not doc_id:
                            continue
                        doc_score = 55.0 + self._score_match(f"{doc.get('title', '')} {doc.get('description', '')}", [search_term]) * 45.0
                        doc["score"] = round(doc_score / 100.0, 4)
                        doc["final_score"] = int(round(doc_score))
                        doc["why_this_video"] = f"Official docs match for {search_term}"
                        doc["score_components"] = {"node_id": node_id, "resource_type": "official_docs", "search_term": search_term}
                        doc["duration_advice"] = scoring_engine.get_duration_advice(0, duration_minutes, request.level)
                        resource_candidates[doc_id] = doc

            if ranked_videos:
                selected_content.extend(ranked_videos)
            if resource_candidates:
                selected_content.extend(sorted(resource_candidates.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)[: max_resources_per_type * 2])

            combined = ranked_videos + sorted(resource_candidates.values(), key=lambda item: float(item.get("final_score", item.get("score", 0.0))), reverse=True)
            scored_by_concept[node["name"]] = combined
            resources_by_node[node_id] = {
                "search_queries": search_queries,
                "items": sorted(resource_candidates.values(), key=lambda item: float(item.get("final_score", item.get("score", 0.0))), reverse=True),
            }

        return scored_by_concept, resources_by_node, search_results_by_node, selected_content, quota_warning

    def build_curriculum(
        self,
        request: GenerateCurriculumRequest,
        progress_callback: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, Any], str, str, list[dict[str, Any]], str]:
        domain = classify_domain(request.goal)
        if progress_callback:
            progress_callback("Calling Groq to design the curriculum")
        plan = self._prompt_with_retry(request, domain=domain)
        if progress_callback:
            progress_callback("Groq returned a draft plan; validating and normalizing it")
        plan = validate_diversity_fit(request.goal, plan)
        nodes = plan["dependency_graph"]["nodes"]
        topic = str(request.goal).strip()
        cache_key = hashlib.sha256(f"v6-groq-urls:{domain}:{topic}:{request.level}:{request.hours_per_day}:{len(nodes)}".encode("utf-8")).hexdigest()
        cache_key = f"curriculum:{cache_key}"

        cached = redis_cache.get_json(cache_key)
        if cached:
            cached_payload = dict(cached)
            cached_payload["cached"] = True
            session_id = str(cached_payload.get("session_id") or request.session_id or "")
            return cached_payload, cached_payload.get("topic", topic), cache_key, [], session_id

        duration_minutes = max(1, int(round(request.hours_per_day * 60)))
        if progress_callback:
            progress_callback("Searching and ranking YouTube videos plus supporting resources")
        scored_by_concept, resources_by_node, search_results_by_node, selected_content, quota_warning = self._execute_strategy(plan, request, duration_minutes, domain)
        if progress_callback:
            progress_callback("Assembling the curriculum weeks and daily plan")

        logger.info("LLM generated %d nodes with %d YouTube search queries.", len(plan['dependency_graph'].get('nodes', [])), sum(len(n.get('search_queries', [])) for n in plan.get('youtube_strategy', {}).get('per_node', [])))

        concepts = [
            {
                "name": node["name"],
                "prerequisites": list(node.get("depends_on", []) or []),
                "estimated_hours": max(0.5, float(node.get("estimated_days", 1)) * request.hours_per_day),
            }
            for node in nodes
        ]

        assembled = path_assembler.assemble(concepts, scored_by_concept, request.hours_per_day, True)
        if progress_callback:
            progress_callback("Finalizing curriculum payload and saving results")
        week_count = len(assembled.get("weeks", []))
        total_videos = sum(len(week.get("days", [])) for week in assembled.get("weeks", []))
        learner_value = self._build_learner_value_bundle(request, nodes, assembled)
        curriculum_payload = {
            "generator_version": "v6-groq-urls",
            "cached": False,
            "topic": topic,
            "level": request.level,
            "hours_per_day": request.hours_per_day,
            "duration_days": assembled.get("duration_days", max(1, len(nodes))),
            "estimated_total_hours": assembled.get("estimated_total_hours", 0),
            "estimated_video_hours": assembled.get("estimated_video_hours", 0),
            "estimated_reading_hours": assembled.get("estimated_reading_hours", 0),
            "estimated_practice_hours": assembled.get("estimated_practice_hours", 0),
            "estimated_review_hours": assembled.get("estimated_review_hours", 0),
            "estimated_buffer_hours": assembled.get("estimated_buffer_hours", 0),
            "description": f"{len(nodes)}-node curriculum for {request.level} learners, generated by Groq and executed by search/ranking services.",
            "warning": quota_warning or None,
            "weeks": assembled.get("weeks", []),
            "session_id": request.session_id or "",
            "can_expand": len(nodes) > week_count * 7,
            "progression_concepts": concepts,
            "llm_plan": plan,
            "search_results_by_node": search_results_by_node,
            "resources_by_node": resources_by_node,
            "projects": plan.get("projects", {}),
            "adaptive_rules": plan.get("adaptive_rules", {}),
            "learner_value": learner_value,
            "domain": domain,
            "diversity_warnings": plan.get("diversity_warnings", []),
            "requires_review": bool(plan.get("requires_review", False)),
        }

        if self._settings.app_debug and assembled.get("_debug"):
            curriculum_payload["debug"] = assembled.get("_debug")

        curriculum_payload["description"] = curriculum_payload["description"] + f" {total_videos} surfaced items across {week_count} weeks."

        redis_cache.set_json(cache_key, curriculum_payload, self._settings.curriculum_cache_ttl_seconds)
        return curriculum_payload, topic, cache_key, selected_content, request.session_id or ""


curriculum_planner = CurriculumPlanner()
