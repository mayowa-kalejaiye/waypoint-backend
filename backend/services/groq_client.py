from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from openai import OpenAI

from backend.config import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class GroqLLMService:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        self._api_key = getattr(settings, "groq_api_key", "")
        self._model = getattr(settings, "groq_model", "llama-3.3-70b-versatile")
        self._timeout_seconds = max(float(settings.llm_timeout_seconds), 45.0)
        self._retry_base_delay_seconds = max(float(settings.llm_retry_base_delay_seconds), 0.5)

        if not self._api_key:
            raise RuntimeError("GROQ_API_KEY is required for Groq-powered features.")

        self._client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=self._api_key,
        )
        self._system_prompt = self._build_system_prompt()
        logger.info("GroqLLMService initialized with timeout=%s seconds", self._timeout_seconds)

    def _build_system_prompt(self) -> str:
        return """You are an expert curriculum designer. Output ONLY valid JSON matching the schema below.
No explanations. No markdown. Start with { and end with }.

Schema:
{
  "dependency_graph": {
    "nodes": [
      {
        "id": "kebab-case-id",
        "name": "Concept name",
        "description": "10-20 word description",
        "duration_days": int,
        "dependencies": ["id1", "id2"],
                "parent_node_id": "id|null",
                "branch_type": "main|branch",
                "continuity_note": "short note about how this node continues the sequence",
        "difficulty": "beginner|intermediate|advanced"
      }
    ]
  },
  "youtube_strategy": {
    "per_node": [
            {
                "node_id": "string",
                "queries": ["query1", "query2", "query3"],
                "preferred_youtube_id": "string (optional YouTube video id the model recommends)",
                                "preferred_youtube_url": "string (required whenever a specific YouTube video is recommended; must be the exact direct watch URL)",
                                "preferred_youtube_title": "string (optional video title if known)",
                                "preferred_youtube_channel": "string (optional channel name if known)",
        "preferred_youtube_query": "string (optional: search query to prefer when no id available)",
        "rubric": {
          "primary_criteria": [{"criterion": "string", "weight": 0-1}],
          "penalties": [{"condition": "string", "points": -1 to -10}],
          "boosters": [{"condition": "string", "points": 1 to 10}]
        },
        "min_score": 50
      }
    ]
  },
  "resources": {
    "per_node": [
      {
        "node_id": "string",
        "resource_types": ["github", "docs", "tutorial", "blog", "podcast", "academic"],
        "search_terms": ["term1", "term2"],
        "authority_domains": ["domain1"],
        "max_resources": 3
      }
    ]
  },
  "projects": {
    "daily_breakdown": [
      {
        "day": int,
        "node_ids": ["id"],
        "title": "Project title",
        "duration_minutes": int,
        "description": "string",
        "deliverable": "string",
        "hints": ["string"],
                "success_criteria": ["criterion1"],
                "external_links": [{"label": "string", "url": "https://example.com", "type": "repo|docs|reference|tool"}]
      }
    ],
    "final_project": {
      "title": "Capstone project",
      "node_ids": ["id"],
      "description": "string",
      "estimated_hours": int,
            "rubric": "string",
            "external_links": [{"label": "string", "url": "https://example.com", "type": "repo|docs|reference|tool"}]
    }
  },
  "adaptive_rules": {
    "if_learner_struggles": {"action": "string", "trigger": "string"},
    "if_learner_accelerates": {"action": "string", "trigger": "string"}
  }
}

Rules:
- dependency_graph is a DAG (no cycles)
- Total duration * hours_per_day = realistic total workload
- Each node description: 10-20 words exactly
- Each project: 3-5 success criteria minimum
- YouTube queries: specific and actionable (include difficulty level)
- For non-tech domains: use pinterest, wikihow, academic, community instead of github
- Rubric criteria must be machine-evaluable (keywords, signals, metrics)
- No generic queries like "how to" - be specific about learning style
- When a project or resource needs an external reference, include an explicit `external_links` array with the exact URL and a short label. Do not invent or sanitize away those URLs."""

    def _build_optimized_user_prompt(self, goal: str, level: str, daily_minutes: int, domain: str) -> str:
        level_instructions = {
            "beginner": "max 8 nodes, 1-2 days each, guided projects, queries include 'beginner tutorial step by step'",
            "intermediate": "10-14 nodes, allow parallel branches, open-ended projects, queries include 'best practices deep dive'",
            "advanced": "12-18 nodes, specification-only projects, queries target edge cases, penalize intro content heavily",
        }

        domain_verbs = {
            "tech_development": "build implement code create deploy",
            "creative_arts": "create make prepare practice document",
            "business_professional": "prepare deliver present write record",
            "health_wellness": "follow practice log complete track",
            "academic_learning": "analyze explain write diagram teach",
            "life_skills": "complete prepare simulate fix organize",
            "humanities": "analyze create interpret compare discuss",
        }

        # Minimum nodes guidance to help the model satisfy our quality gate
        min_nodes_map = {"beginner": 5, "intermediate": 8, "advanced": 10}
        min_required = min_nodes_map.get(level, 5)

        # Add an explicit single-line domain-alignment instruction to reduce cross-domain projects
        domain_align = (
            f"DomainAlignment: Ensure generated projects and resource links are appropriate for domain={domain}. "
            "Do NOT propose GitHub/code-based projects for non-tech domains; ensure project titles and resource URLs directly relate to the topic."
        )

        return f"""Topic: {goal}
      Level: {level}
      Daily: {daily_minutes}min
      Domain: {domain}
      Style: {level_instructions.get(level, 'create')}
      Verbs: {domain_verbs.get(domain, 'create complete practice')}
      ---
      {domain_align}
            Produce JSON that strictly follows the system schema and includes a `dependency_graph.nodes` array with at least {min_required} nodes.
      Each node MUST include the fields: `id`, `name`, `description`, `duration_days`, `dependencies`, `difficulty`.
    Nodes should remain sequential and tree-like: a single parent may branch into multiple child nodes, but each child must still preserve continuity with the parent sequence.
    If a node branches, include `parent_node_id`, set `branch_type` to `branch`, and write a brief `continuity_note` explaining what continues from the parent.
      No explanations, no markdown, no extra fields outside the schema. Output only valid JSON matching the schema.

                        Waypoint can make mistakes, so do not over-broaden the topic. Prefer one strong, directly relevant result per node over many weak ones.
                        Do not suggest unrelated adjacent platforms or technologies unless they are explicitly required by the topic.

        This is an educational platform focused on learner development. Prioritize depth, usefulness, and instructional value over short clips.
        For every YouTube recommendation, strongly prefer long-form videos that are 60 minutes or longer, such as full lectures, complete courses, masterclasses, and deep dives.
        Avoid 10-17 minute videos unless there is truly no long-form option available for that concept.

        When you can identify a specific video, you MUST include `preferred_youtube_url` as the exact direct YouTube watch URL and keep `preferred_youtube_id` in sync with it.
        Do not invent or guess URLs. If you are not confident about the exact video URL, leave both `preferred_youtube_url` and `preferred_youtube_id` blank and do not recommend a video.
      
        YouTube guidance: When recommending videos, prefer longer, authoritative videos (>= 60 minutes) from well-known or highly-subscribed channels to increase credibility.
            For each `youtube_strategy.per_node` entry, include the following helpful fields when possible:
            - `preferred_youtube_id`: specific YouTube ID the model strongly recommends (optional)
            - `preferred_youtube_url`: direct YouTube watch URL for that recommendation (required for any specific video recommendation)
            - `preferred_youtube_title`: exact video title when known (optional)
            - `preferred_youtube_channel`: channel name when known (optional)
            - `preferred_youtube_query`: a search query tuned to find authoritative sources (include channel names and terms like "lecture", "full lecture", "deep dive", "official", "course").
            - `authority_hints`: an array of channel name hints (e.g. ["MIT OpenCourseWare", "Stanford Engineering", "3Blue1Brown", "Two Minute Papers"]) when domain-appropriate.
            - `preferred_recency_years`: integer indicating the recency window (e.g. 3 means prefer videos published within last 3 years) and a short `recency_rationale` string.
            - `recency_priority`: one of "recent|balanced|timeless" indicating whether recency should be prioritized.
            - `ranking_hint`: an optional numeric suggestion (0-100) indicating expected priority for this node.

            Add an explicit ranking preference in `youtube_strategy.per_node` that favors videos where `duration_seconds` >= 3600 and `view_count` is high; include `preferred_youtube_url` for the exact video whenever one is known, `preferred_youtube_id` when available, and a `preferred_youtube_query` targeting authoritative channel names or formats ("full lecture", "full course", "masterclass", "deep dive").

            When possible, include a short explanation field `evidence` listing why the suggested video/query is authoritative (e.g. "university lecture", "official channel", "high subscriber count", "recent conference talk").

            Generate now."""

    def _extract_json_from_response(self, text: str) -> dict[str, Any]:
        text = text.strip()
        text = re.sub(r'^```json\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
        text = re.sub(r'^```\n?', '', text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            text = re.sub(r',(\s*[}\]])', r'\1', text)
            text = re.sub(r'(?<!")(\w[\w-]*)(\s*):', r'"\1"\2:', text)
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning("JSON parse error after fixes: %s. Response snippet: %s", str(exc), text[:500])
                raise ValueError(f"Cannot parse response JSON: {str(exc)}") from exc

    def _normalize_schema(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Normalize common LLM field-name variants to our expected schema."""
        if not isinstance(plan, dict):
            return plan

        dg = plan.get("dependency_graph")
        if isinstance(dg, dict):
            nodes = dg.get("nodes")
            if isinstance(nodes, list):
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    # description aliases
                    if "desc" in node and "description" not in node:
                        node["description"] = node.pop("desc")
                    # duration aliases
                    if "estimated_days" in node and "duration_days" not in node:
                        node["duration_days"] = node.pop("estimated_days")
                    if "days" in node and "duration_days" not in node:
                        node["duration_days"] = node.pop("days")
                    # dependencies aliases
                    if "depends_on" in node and "dependencies" not in node:
                        node["dependencies"] = node.pop("depends_on")
                    # ensure duration is int
                    if "duration_days" in node:
                        try:
                            node["duration_days"] = int(node["duration_days"])
                        except Exception:
                            node["duration_days"] = 1

        # Normalize youtube_strategy.per_node entries
        ys = plan.get("youtube_strategy")
        if isinstance(ys, dict):
            per = ys.get("per_node")
            if isinstance(per, list):
                for item in per:
                    if not isinstance(item, dict):
                        continue
                    if "queries" in item and "search_queries" not in item:
                        item["search_queries"] = item.pop("queries")
                    # keep explicit external-link fields if the model supplied them
                    for alias in ("external_links", "resource_links", "reference_links"):
                        if alias in item and item[alias] is None:
                            item[alias] = []
                    if "preferred_youtube_id" in item and item.get("preferred_youtube_id") is None:
                        item["preferred_youtube_id"] = ""
                    if "preferred_youtube_url" in item and item.get("preferred_youtube_url") is None:
                        item["preferred_youtube_url"] = ""
                    if "preferred_youtube_title" in item and item.get("preferred_youtube_title") is None:
                        item["preferred_youtube_title"] = ""
                    if "preferred_youtube_channel" in item and item.get("preferred_youtube_channel") is None:
                        item["preferred_youtube_channel"] = ""
                    if "preferred_youtube_query" in item and item.get("preferred_youtube_query") is None:
                        item["preferred_youtube_query"] = ""

                    # Accept video URL variants from Groq and convert them to preferred_youtube_id
                    try:
                        # common fields that models might use
                        for url_field in ("preferred_youtube_url", "youtube_url", "video_url"):
                            if url_field in item and item.get(url_field):
                                url_val = str(item.get(url_field) or "")
                                item["preferred_youtube_url"] = url_val
                                m = re.search(r"(?:v=|youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,})", url_val)
                                if m:
                                    item["preferred_youtube_id"] = m.group(1)
                                    break
                                # fallback: last path segment might be the id
                                m2 = re.search(r"([A-Za-z0-9_-]{6,})$", url_val)
                                if m2:
                                    item["preferred_youtube_id"] = m2.group(1)
                                    break

                        # Also inspect external_links arrays for a YouTube URL
                        ext = item.get("external_links") or item.get("resource_links") or item.get("reference_links") or []
                        if isinstance(ext, list):
                            for link in ext:
                                try:
                                    if not isinstance(link, dict):
                                        continue
                                    link_url = str(link.get("url", "") or "")
                                    if "youtube.com" in link_url or "youtu.be" in link_url:
                                        item["preferred_youtube_url"] = link_url
                                        m3 = re.search(r"(?:v=|youtu\.be/|youtube\.com/watch\?v=)([A-Za-z0-9_-]{6,})", link_url)
                                        if m3:
                                            item["preferred_youtube_id"] = m3.group(1)
                                            break
                                except Exception:
                                    continue
                    except Exception:
                        # non-fatal normalization step
                        pass

                    if item.get("preferred_youtube_id") and not item.get("preferred_youtube_url"):
                        item["preferred_youtube_url"] = f"https://www.youtube.com/watch?v={item['preferred_youtube_id']}"

        # Ensure resource_strategy has per_node entries (no-op if already present)
        rs = plan.get("resource_strategy")
        if isinstance(rs, dict):
            per = rs.get("per_node")
            if not isinstance(per, list):
                rs["per_node"] = []

        return plan

    def structured_json(self, prompt: str, max_retries: int | None = None) -> dict[str, Any]:
        retries = max(1, max_retries if max_retries is not None else int(self._settings.llm_max_retries))
        delay_seconds = self._retry_base_delay_seconds
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": "Return valid minified JSON only. Do not include code fences."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                  max_tokens=6500,
                    response_format={"type": "json_object"},
                    timeout=self._timeout_seconds,
                )
                content = response.choices[0].message.content if response.choices else None
                if not content:
                    raise ValueError("Groq returned an empty response")
                usage = getattr(response, "usage", None)
                if usage is not None:
                    logger.info(
                        "Groq usage - input: %s, output: %s, total: %s",
                        getattr(usage, "prompt_tokens", "n/a"),
                        getattr(usage, "completion_tokens", "n/a"),
                        getattr(usage, "total_tokens", "n/a"),
                    )
                plan = self._extract_json_from_response(content)
                try:
                  plan = self._normalize_schema(plan)
                except Exception as exc:
                  logger.warning("Failed to normalize LLM schema: %s", exc)
                return plan
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.exception("Groq structured_json attempt %d/%d failed: %s", attempt + 1, retries, exc)
                if attempt < retries - 1:
                    time.sleep(delay_seconds)
                    delay_seconds *= 2

        raise RuntimeError(f"Groq structured JSON call failed after retries: {last_error}") from last_error

    def call_groq(self, goal: str, level: str, daily_minutes: int, domain: str) -> dict[str, Any]:
        prompt = self._build_optimized_user_prompt(goal, level, daily_minutes, domain)
        logger.info("Calling Groq for: %s (level=%s)", goal, level)
        curriculum = self.structured_json(prompt, max_retries=self._settings.llm_max_retries)
        logger.info("Groq call succeeded for: %s", goal)
        return curriculum

    def validate_curriculum_quality(self, curriculum: dict[str, Any], level: str) -> bool:
        expected_min_nodes = {
            "beginner": 5,
            "intermediate": 8,
            "advanced": 10,
        }

        nodes = curriculum.get("dependency_graph", {}).get("nodes", [])
        min_required = expected_min_nodes.get(level, 5)

        if len(nodes) < min_required:
            logger.warning("Low node count: %d < %d (level=%s)", len(nodes), min_required, level)
            return False

        for node in nodes:
            required_fields = ["id", "name", "description", "duration_days", "dependencies", "difficulty"]
            if not all(k in node for k in required_fields):
                logger.warning("Node missing required fields: %s", node)
                return False

        strategies = curriculum.get("youtube_strategy", {}).get("per_node", [])
        if len(strategies) < len(nodes) // 2:
            logger.warning("Low YouTube strategy coverage: %d strategies for %d nodes", len(strategies), len(nodes))
            return False

        logger.info("Curriculum quality validation passed: %d nodes, level=%s", len(nodes), level)
        return True


groq_service = GroqLLMService()
