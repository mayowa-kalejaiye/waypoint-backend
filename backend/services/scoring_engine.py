from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from enum import Enum
import logging
import re

from backend.services.domain_classifier import classify_domain


class ConceptTypeAffinity(Enum):
    """Type of concept for source routing."""
    FOUNDATIONAL = "foundational"  # Best: YouTube, then docs
    REFERENCE = "reference"  # Best: Docs, then GitHub, then YouTube
    PRACTICAL = "practical"  # Best: GitHub repos, then YouTube with code
    THEORETICAL = "theoretical"  # Best: ArXiv papers, then YouTube
    MIXED = "mixed"  # Use default weights


class ScoringEngine:
    logger = logging.getLogger(__name__)

    LEVEL_DURATION_PROFILES: dict[str, dict[str, float]] = {
        "beginner": {
            "ideal_min": 5.0,
            "ideal_max": 20.0,
            "absolute_max": 45.0,
            "under_penalty": 5.0,
            "over_penalty": 15.0,
            "like_target": 0.95,
        },
        "intermediate": {
            "ideal_min": 10.0,
            "ideal_max": 30.0,
            "absolute_max": 60.0,
            "under_penalty": 3.0,
            "over_penalty": 8.0,
            "like_target": 0.97,
        },
        "advanced": {
            "ideal_min": 15.0,
            "ideal_max": 50.0,
            "absolute_max": 120.0,
            "under_penalty": 10.0,
            "over_penalty": 5.0,
            "like_target": 0.98,
        },
    }
    
    # Source affinity scores: (source → score boost/penalty) for each concept type
    SOURCE_AFFINITY: dict[ConceptTypeAffinity, dict[str, float]] = {
        ConceptTypeAffinity.FOUNDATIONAL: {
            "youtube": 1.15,      # Strong boost for videos
            "docs": 0.95,         # Slight boost for docs
            "arxiv": 0.70,        # Penalize papers
            "github": 0.85,       # Penalize repos
        },
        ConceptTypeAffinity.REFERENCE: {
            "docs": 1.25,         # Strong boost for docs
            "github": 1.00,       # Neutral boost for repos
            "youtube": 0.90,      # Slight penalty for videos
            "arxiv": 0.60,        # Penalize papers
        },
        ConceptTypeAffinity.PRACTICAL: {
            "github": 1.30,       # Strong boost for repos
            "youtube": 1.05,      # Slight boost for videos with code
            "docs": 0.80,         # Penalty for docs
            "arxiv": 0.50,        # Strong penalty for papers
        },
        ConceptTypeAffinity.THEORETICAL: {
            "arxiv": 1.35,        # Strong boost for papers
            "youtube": 0.95,      # Slight penalty for videos
            "docs": 0.75,         # Penalty for docs
            "github": 0.60,       # Strong penalty for repos
        },
        ConceptTypeAffinity.MIXED: {
            "youtube": 1.00,      # Neutral
            "docs": 1.00,
            "github": 1.00,
            "arxiv": 1.00,
        },
    }
    
    def _infer_concept_type(self, concept: str) -> ConceptTypeAffinity:
        """Infer concept type from name to route to appropriate sources."""
        concept_lower = concept.lower()
        
        # Practical keywords: build, code, project, implement, create, practice, exercise, build
        practical_keywords = {"build", "code", "project", "implement", "create", "practice", "exercise", "workshop", "assignment", "hands-on", "develop"}
        if any(kw in concept_lower for kw in practical_keywords):
            return ConceptTypeAffinity.PRACTICAL
        
        # Theoretical keywords: theory, concept, principle, math, algorithm, formal, proof, research
        theoretical_keywords = {"theory", "concept", "principle", "math", "algorithm", "formal", "proof", "research", "analysis", "study", "advanced"}
        if any(kw in concept_lower for kw in theoretical_keywords):
            return ConceptTypeAffinity.THEORETICAL
        
        # Reference keywords: api, syntax, reference, documentation, spec, format, protocol, standard
        reference_keywords = {"api", "syntax", "reference", "documentation", "spec", "format", "protocol", "standard", "guide", "tutorial", "how to"}
        if any(kw in concept_lower for kw in reference_keywords):
            return ConceptTypeAffinity.REFERENCE
        
        # Foundational keywords: basics, intro, introduction, fundamentals, start, get started, learn
        foundational_keywords = {"basics", "intro", "introduction", "fundamentals", "start", "getting started", "learn", "understand", "overview"}
        if any(kw in concept_lower for kw in foundational_keywords):
            return ConceptTypeAffinity.FOUNDATIONAL
        
        # Default to foundational for safe assumption
        return ConceptTypeAffinity.FOUNDATIONAL
    
    def _calculate_source_affinity_boost(self, source: str, concept: str) -> float:
        """Calculate affinity boost for a given source and concept."""
        concept_type = self._infer_concept_type(concept)
        affinity_map = self.SOURCE_AFFINITY.get(concept_type, self.SOURCE_AFFINITY[ConceptTypeAffinity.MIXED])
        
        # Normalize source name (e.g., "youtube" vs "yt")
        source_normalized = source.lower().strip() if source else "youtube"
        
        return affinity_map.get(source_normalized, 1.0)
    
    def _calculate_quality_score(self, video: dict[str, Any]) -> float:
        """Calculate content quality score from 0-1, penalizing clickbait and red flags."""
        quality_score = 0.8  # Default baseline
        
        # Clickbait detection
        title_lower = str(video.get("title", "")).lower()
        description_lower = str(video.get("description", "")).lower()
        
        # Clickbait red flags
        clickbait_terms = {
            "shocking": 0.15,
            "insane": 0.12,
            "unbelievable": 0.12,
            "destroyed": 0.12,
            "exposed": 0.10,
            "meme": 0.08,
            "prank": 0.10,
            "reaction": 0.08,
            "drama": 0.10,
            "gone wrong": 0.10,
            "rekt": 0.08,
            "clickbait": 0.20,
        }
        
        for term, penalty in clickbait_terms.items():
            if term in title_lower:
                quality_score -= penalty * 1.5  # Title is more important
            elif term in description_lower:
                quality_score -= penalty * 0.5
        
        # Engagement ratio anomaly (high views, no likes/engagement)
        view_count = max(video.get("view_count", 1), 1)
        like_count = max(video.get("like_count", 0), 0)
        like_ratio = like_count / view_count
        
        # Normal like ratio is 0.5-5% of views; if much lower, it's suspicious
        if like_ratio < 0.0005:  # Very low engagement
            quality_score -= 0.05  # Reduced penalty for suspicious engagement
        
        # Duration red flags
        duration_seconds = video.get("duration_seconds", 0)
        if duration_seconds > 0:
            # If claimed to be a "complete course" or "masterclass" but <30 minutes, red flag
            # Relaxed minimums: allow shorter high-quality resources to remain valid
            title_indicators = {
                "complete": 8 * 60,
                "masterclass": 12 * 60,
                "course": 18 * 60,
                "tutorial": 6 * 60,
            }
            for indicator, min_duration in title_indicators.items():
                if indicator in title_lower and duration_seconds < min_duration:
                    quality_score -= 0.04
        
        # Channel reputation (view velocity)
        # If very new video with few views (< 100), less established
        if view_count < 100 and duration_seconds > 0:
            quality_score -= 0.03
        
        # Audio quality indicator (lack of description or no captions)
        if not video.get("description") or len(str(video.get("description", ""))) < 50:
            quality_score -= 0.03  # Slight penalty for poor documentation
        
        # Clamp to 0-1 range
        return max(0.0, min(1.0, quality_score))
    def _normalize(self, value: float, upper: float) -> float:
        if upper <= 0:
            return 0.0
        return max(0.0, min(1.0, value / upper))

    def _contains_any(self, text: str, tokens: list[str]) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in tokens)

    def _parse_published_at(self, published_at: str) -> datetime | None:
        try:
            return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except Exception:
            return None

    def _duration_profile(self, level: str) -> dict[str, float]:
        return self.LEVEL_DURATION_PROFILES.get(level.lower().strip(), self.LEVEL_DURATION_PROFILES["beginner"])

    def _domain_signals(self, domain: str) -> dict[str, Any]:
        domain_name = (domain or "").lower().strip()
        if domain_name == "tech_development":
            return {
                "depth_keywords": ["code", "example", "walkthrough", "project", "implementation"],
                "practice_keywords": ["github", "code", "example", "walkthrough"],
                "breakdown_key": "code_keywords",
                "breakdown_note_positive": "Code/example/walkthrough signals detected.",
                "breakdown_note_negative": "No code-oriented keywords detected.",
                "reason_phrase": "includes code-oriented examples",
                "boost_component": "code_boost",
            }
        return {
            "depth_keywords": ["demonstration", "guided", "practice", "technique", "example"],
            "practice_keywords": ["demonstration", "practice", "example", "checklist"],
            "breakdown_key": "practice_signals",
            "breakdown_note_positive": "Practice/demonstration signals detected.",
            "breakdown_note_negative": "No practice-oriented signals detected.",
            "reason_phrase": "includes practical demonstrations",
            "boost_component": "practice_boost",
        }

    def get_duration_advice(self, video_duration_minutes: float, daily_budget_minutes: float, level: str) -> str:
        profile = self._duration_profile(level)
        duration = max(0.0, float(video_duration_minutes))
        budget = max(1.0, float(daily_budget_minutes))

        if level.lower().strip() == "advanced" and duration < profile["ideal_min"]:
            return "Very short for advanced - this may be too basic"
        if duration <= min(budget, profile["ideal_max"]):
            return "Perfect fit for your daily budget"
        if duration <= budget * 1.25:
            return "Slightly long - consider splitting into two sessions"
        if duration > profile["absolute_max"]:
            return "Too long for this level - find a shorter alternative"
        if duration > budget:
            return "Slightly long - consider splitting into two sessions"
        return "Perfect fit for your daily budget"

    def _duration_penalty_and_note(self, duration_minutes: float, level: str) -> tuple[float, str]:
        profile = self._duration_profile(level)
        duration = max(0.0, duration_minutes)

        if duration < profile["ideal_min"]:
            minutes_under = profile["ideal_min"] - duration
            penalty = minutes_under * profile["under_penalty"]
            return penalty, f"{minutes_under:.1f} min under ideal range"

        if duration > profile["absolute_max"]:
            minutes_over = duration - profile["absolute_max"]
            penalty = min(80.0, minutes_over * 10.0)
            return penalty, f"{minutes_over:.1f} min over absolute max"

        if duration > profile["ideal_max"]:
            minutes_over = duration - profile["ideal_max"]
            penalty = minutes_over * profile["over_penalty"]
            return penalty, f"{minutes_over:.1f} min over ideal range"

        return 0.0, "Within ideal duration range"

    def _like_ratio_score(self, like_ratio: float, target: float) -> tuple[float, str]:
        if like_ratio <= 0:
            return 0.0, "No like data available"
        closeness = max(0.0, 1.0 - abs(like_ratio - target) / max(target, 0.01))
        return min(15.0, closeness * 15.0), f"Like ratio {like_ratio:.1%} vs target {target:.1%}"

    def _rubric_metric(self, criterion: str, video: dict[str, Any], analysis: dict[str, Any], concept: str, level: str) -> float:
        text = f"{video.get('title', '')} {video.get('description', '')} {video.get('channel', '')}".lower()
        concept_lower = concept.lower()
        criterion_lower = criterion.lower().strip()

        if criterion_lower in {"relevance_to_subtopic", "relevance"}:
            terms = [token for token in concept_lower.split() if len(token) > 2]
            return min(100.0, 100.0 * self._normalize(sum(1 for term in terms if term in text), max(len(terms), 1)))
        if criterion_lower in {"content_density", "density"}:
            density = 0.0
            if analysis.get("transcript_available"):
                density += 45.0
            if len(str(video.get("description", ""))) > 160:
                density += 20.0
            if self._contains_any(text, ["code", "exercise", "walkthrough", "project", "implementation"]):
                density += 35.0
            return min(100.0, density)
        if criterion_lower in {"engagement_quality", "engagement"}:
            view_count = max(float(video.get("view_count", 0) or 0), 1.0)
            like_count = max(float(video.get("like_count", 0) or 0), 0.0)
            ratio = like_count / view_count
            return min(100.0, ratio * 2500.0)
        if criterion_lower in {"freshness", "recency"}:
            published = self._parse_published_at(str(video.get("published_at", "")))
            if not published:
                return 40.0
            age_days = max(0, (datetime.now(timezone.utc) - published).days)
            if age_days <= 180:
                return 100.0
            if age_days <= 365:
                return 95.0
            if age_days <= 730:
                return 85.0
            return 65.0
        if criterion_lower in {"duration_alignment", "length", "ideal_length"}:
            duration_minutes = max(0.0, float(video.get("duration_seconds", 0) or 0) / 60.0)
            profile = self._duration_profile(level)
            if profile["ideal_min"] <= duration_minutes <= profile["ideal_max"]:
                return 100.0
            if duration_minutes < profile["ideal_min"]:
                return max(0.0, 100.0 - ((profile["ideal_min"] - duration_minutes) * profile["under_penalty"] * 2.0))
            if duration_minutes <= profile["absolute_max"]:
                return max(0.0, 100.0 - ((duration_minutes - profile["ideal_max"]) * profile["over_penalty"] * 2.0))
            return max(0.0, 20.0 - ((duration_minutes - profile["absolute_max"]) * 5.0))
        if criterion_lower in {"advanced_depth", "level_alignment"}:
            advanced_terms = ["advanced", "deep dive", "architecture", "optimization", "edge case", "production"]
            beginner_terms = ["for beginners", "beginner", "intro", "introduction", "what is", "basics", "overview"]
            if self._contains_any(text, advanced_terms):
                return 100.0
            if self._contains_any(text, beginner_terms):
                return 5.0 if level.lower().strip() == "advanced" else 35.0
            return 60.0
        return 50.0

    def _matches_condition(
        self,
        condition: str,
        video: dict[str, Any],
        analysis: dict[str, Any],
        concept: str,
        level: str,
        duration_minutes: float,
    ) -> bool:
        text = f"{video.get('title', '')} {video.get('description', '')} {video.get('channel', '')}".lower()
        condition_lower = condition.lower()
        if "longer than" in condition_lower and "minute" in condition_lower:
            numbers = re.findall(r"\d+(?:\.\d+)?", condition_lower)
            threshold = float(numbers[0]) if numbers else duration_minutes
            return duration_minutes > threshold
        if "downloadable code" in condition_lower or "includes code" in condition_lower:
            return self._contains_any(text, ["code", "github", "walkthrough", "example"])
        if "transcript" in condition_lower or "captions" in condition_lower:
            return bool(video.get("transcript_available")) or bool(analysis.get("transcript_available"))
        if "beginner" in condition_lower and level.lower().strip() == "advanced":
            return self._contains_any(text, ["for beginners", "beginner", "what is", "introduction", "basics", "overview"])
        return False

    def _apply_rubric(
        self,
        video: dict[str, Any],
        analysis: dict[str, Any],
        concept: str,
        level: str,
        rubric: dict[str, Any] | None,
        duration_minutes: float,
    ) -> tuple[float, list[dict[str, Any]]]:
        if not rubric:
            return 0.0, []

        breakdown: list[dict[str, Any]] = []
        weighted_total = 0.0
        weight_sum = 0.0

        primary_criteria = rubric.get("primary_criteria", []) if isinstance(rubric.get("primary_criteria", []), list) else []
        for item in primary_criteria:
            if not isinstance(item, dict):
                continue
            criterion = str(item.get("criterion", "relevance_to_subtopic"))
            try:
                weight = max(0.0, float(item.get("weight", 0.0)))
            except Exception:
                weight = 0.0
            ideal_threshold = item.get("ideal_threshold")
            metric = self._rubric_metric(criterion, video, analysis, concept, level)

            if isinstance(ideal_threshold, (int, float)):
                threshold_note = f"target {ideal_threshold}"
            else:
                threshold_note = f"target {ideal_threshold}" if ideal_threshold is not None else "no threshold"

            weighted_total += metric * weight
            weight_sum += weight
            breakdown.append(
                {
                    "criterion": criterion,
                    "score": round(metric, 2),
                    "weight": round(weight, 3),
                    "note": threshold_note,
                }
            )

        score = (weighted_total / weight_sum) if weight_sum > 0 else 0.0

        penalties = rubric.get("penalties", []) if isinstance(rubric.get("penalties", []), list) else []
        for item in penalties:
            if not isinstance(item, dict):
                continue
            condition = str(item.get("condition", ""))
            try:
                penalty = float(item.get("penalty", 0.0))
            except Exception:
                penalty = 0.0
            if condition and self._matches_condition(condition, video, analysis, concept, level, duration_minutes):
                score -= penalty
                breakdown.append({"criterion": f"penalty:{condition}", "score": -round(penalty, 2), "weight": 0.0, "note": "Applied from rubric"})

        boosters = rubric.get("boosters", []) if isinstance(rubric.get("boosters", []), list) else []
        for item in boosters:
            if not isinstance(item, dict):
                continue
            condition = str(item.get("condition", ""))
            try:
                boost = float(item.get("boost", 0.0))
            except Exception:
                boost = 0.0
            if condition and self._matches_condition(condition, video, analysis, concept, level, duration_minutes):
                score += boost
                breakdown.append({"criterion": f"boost:{condition}", "score": round(boost, 2), "weight": 0.0, "note": "Applied from rubric"})

        return score, breakdown

    def _beginner_title_signal(self, title: str) -> float:
        title_lower = title.lower()
        tokens = ["beginner", "explained", "crash course", "basics", "introduction"]
        return min(1.0, sum(0.25 for token in tokens if token in title_lower))

    def _recency_score(self, published_at: str) -> float:
        try:
            dt_value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_days = max((now - dt_value).days, 0)
            if age_days <= 30 * 18:
                return 1.0
            if age_days >= 30 * 48:
                return 0.2
            return max(0.2, 1 - ((age_days - (30 * 18)) / (30 * 30)))
        except Exception:
            return 0.5

    def score_video(
        self,
        video: dict[str, Any],
        analysis: dict[str, Any],
        concept: str,
        performance_score: float = 0.5,
        personalization_weights: dict[str, float] | None = None,
        level: str = "beginner",
        rubric: dict[str, Any] | None = None,
        daily_budget_minutes: float | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """Score a video using level-aware duration preferences and optional rubric overrides."""
        concept_terms = [token for token in concept.lower().split() if len(token) > 2]
        title_text = str(video.get("title", "")).lower()
        description_text = str(video.get("description", "")).lower()
        haystack = f"{title_text} {description_text}"
        source = str(video.get("source", "youtube")).lower()
        level_name = level.lower().strip()
        profile = self._duration_profile(level_name)
        domain_name = domain or classify_domain(concept)
        domain_signals = self._domain_signals(domain_name)

        weights = {
            "relevance": 0.30,
            "depth": 0.18,
            "engagement_quality": 0.12,
            "recency": 0.10,
            "performance_score": 0.10,
            "duration_fit": 0.20,
        }
        if personalization_weights:
            for key in weights:
                if key in personalization_weights:
                    weights[key] = personalization_weights[key]

        duration_minutes = max(0.0, float(video.get("duration_seconds", 0) or 0) / 60.0)
        daily_budget_minutes = daily_budget_minutes if daily_budget_minutes is not None else max(duration_minutes, 1.0)
        duration_penalty, duration_note = self._duration_penalty_and_note(duration_minutes, level_name)

        lexical_hits = sum(1 for term in concept_terms if term in haystack)
        exact_phrase_hit = 1.0 if concept.lower() in title_text else 0.0
        title_term_hit = 1.0 if any(term in title_text for term in concept_terms) else 0.0
        description_term_hit = 1.0 if any(term in description_text for term in concept_terms) else 0.0
        transcript_available = bool(video.get("transcript_available") or analysis.get("transcript_available"))

        relevance_signal = min(100.0, (
            lexical_hits * 18.0
            + exact_phrase_hit * 35.0
            + title_term_hit * 18.0
            + description_term_hit * 8.0
            + self._normalize(float(analysis.get("relevance_overlap", 0.0)), 2.5) * 25.0
        ))

        if source == "youtube" and lexical_hits == 0:
            relevance_signal *= 0.5

        density_signal = 0.0
        if transcript_available:
            density_signal += 20.0
        if len(description_text) > 160:
            density_signal += 10.0
        if self._contains_any(haystack, domain_signals["depth_keywords"]):
            density_signal += 15.0
        if self._contains_any(haystack, ["advanced", "deep dive", "architecture", "optimization", "edge case", "production"]):
            density_signal += 10.0
        if level_name == "advanced" and self._contains_any(haystack, ["for beginners", "beginner", "what is", "introduction", "basics", "overview"]):
            density_signal -= 90.0

        reading_level = float(analysis.get("reading_level", 0.0))
        reading_score = 100.0 if reading_level < 5 else max(0.0, 100.0 - ((reading_level - 5.0) * 4.0))

        transcript_score = 25.0 if transcript_available else 0.0
        transcript_coverage = min(10.0, self._normalize(float(analysis.get("transcript_word_count", 0.0)), 2000) * 10.0)

        view_count = max(float(video.get("view_count", 0) or 0), 1.0)
        like_count = max(float(video.get("like_count", 0) or 0), 0.0)
        like_ratio = like_count / view_count
        like_target = profile["like_target"]
        like_score, like_note = self._like_ratio_score(like_ratio, like_target)

        published_value = self._parse_published_at(str(video.get("published_at", "")))
        if published_value:
            age_days = max(0, (datetime.now(timezone.utc) - published_value).days)
        else:
            age_days = 9999
        channel_age_score = 0.0
        if age_days >= 365:
            channel_age_score = 10.0
        elif age_days >= 180:
            channel_age_score = 5.0

        practice_keywords = list(domain_signals["practice_keywords"])
        practice_boost = min(15.0, sum(5.0 for token in practice_keywords if token in haystack))

        engagement_quality = min(100.0, like_score + min(20.0, (float(video.get("view_count", 0) or 0) / 50000.0) * 10.0))
        recency = self._recency_score(str(video.get("published_at", ""))) * 100.0
        performance_adj = max(0.0, min(100.0, performance_score * 100.0))
        
        # Long-form video boost: strongly prefer videos 60+ minutes for in-depth learning
        long_form_boost = 0.0
        if duration_minutes >= 60:
            long_form_boost = 28.0  # Major boost for full courses/long-form content
        elif duration_minutes >= 45:
            long_form_boost = 18.0
        elif duration_minutes >= 30:
            long_form_boost = 10.0

        rubric_score, rubric_breakdown = self._apply_rubric(video, analysis, concept, level_name, rubric, duration_minutes)

        if rubric:
            base_score = rubric_score
        else:
            duration_fit_score = max(0.0, 100.0 - duration_penalty)
            if duration_minutes == 0:
                duration_fit_score = 40.0

            score = (
                relevance_signal * weights["relevance"]
                + density_signal * weights["depth"]
                + engagement_quality * weights["engagement_quality"]
                + recency * weights["recency"]
                + performance_adj * weights["performance_score"]
                + duration_fit_score * weights["duration_fit"]
                + transcript_score
                + transcript_coverage
                + channel_age_score
                + practice_boost
                + long_form_boost
            )

            if level_name == "advanced" and self._contains_any(haystack, ["for beginners", "beginner", "what is", "introduction", "basics", "overview"]):
                score -= 90.0

            base_score = score

        if duration_minutes > daily_budget_minutes:
            overshoot = duration_minutes - daily_budget_minutes
            if overshoot > 0:
                base_score -= min(30.0, overshoot * 2.0)

        base_score -= duration_penalty
        base_score = max(0.0, min(100.0, base_score))

        if base_score >= 85.0:
            recommended_action = "top_pick"
        elif base_score >= 70.0:
            recommended_action = "good_alternative"
        elif base_score >= 50.0:
            recommended_action = "acceptable_if_no_better"
        elif duration_minutes > daily_budget_minutes and duration_minutes > 0:
            recommended_action = "too_long_split_across_days"
        else:
            recommended_action = "skip"

        if level_name == "advanced" and duration_minutes < profile["ideal_min"]:
            recommended_action = "skip" if base_score < 60.0 else "acceptable_if_no_better"

        if duration_minutes > daily_budget_minutes:
            duration_advice = f"This video is longer than your daily budget. Watch first {int(daily_budget_minutes)} minutes today, remaining {max(1, int(round(duration_minutes - daily_budget_minutes)))} minutes tomorrow."
        else:
            duration_advice = self.get_duration_advice(duration_minutes, daily_budget_minutes, level_name)

        beginner_flags = self._contains_any(haystack, ["for beginners", "beginner", "what is", "introduction", "basics", "overview"])
        if level_name == "advanced" and beginner_flags:
            recommended_action = "skip"

        final_score = int(round(base_score))
        normalized_score = round(final_score / 100.0, 4)

        breakdown: list[dict[str, Any]] = [
            {"criterion": "relevance", "score": round(relevance_signal, 2), "note": f"Lexical match across title, description, and concept terms ({len(concept_terms)} terms)."},
            {"criterion": "duration_fit", "score": round(max(0.0, 100.0 - duration_penalty), 2), "note": duration_note},
            {"criterion": "transcript", "score": round(transcript_score, 2), "note": "Transcript/captions availability bonus." if transcript_available else "No transcript available."},
            {"criterion": "like_ratio", "score": round(like_score, 2), "note": like_note},
            {"criterion": "channel_age", "score": round(channel_age_score, 2), "note": "Channel/video age bonus." if channel_age_score else "No age bonus."},
            {
                "criterion": str(domain_signals["breakdown_key"]),
                "score": round(practice_boost, 2),
                "note": str(domain_signals["breakdown_note_positive"]) if practice_boost else str(domain_signals["breakdown_note_negative"]),
            },
        ]
        
        # Add long-form boost to breakdown if applicable
        if long_form_boost > 0:
            breakdown.append({
                "criterion": "long_form_boost",
                "score": round(long_form_boost, 2),
                "note": f"Long-form content boost: {int(duration_minutes)} min video strongly preferred for in-depth learning and full-course coverage."
            })

        if rubric_breakdown:
            breakdown.extend(rubric_breakdown)

        if level_name == "advanced" and beginner_flags:
            breakdown.append({"criterion": "advanced_gate", "score": -90.0, "note": "Beginner-oriented language is disqualifying for advanced learners."})

        reason_parts = []
        if relevance_signal >= 70:
            reason_parts.append(f"Strong match for {concept}")
        elif relevance_signal >= 45:
            reason_parts.append(f"Relevant to {concept}")
        else:
            reason_parts.append(f"Only partially matches {concept}")

        if duration_minutes < profile["ideal_min"] and level_name == "advanced":
            reason_parts.append("too short for advanced depth")
        elif duration_minutes > profile["ideal_max"]:
            reason_parts.append("long-form coverage")
        else:
            reason_parts.append("good duration fit")

        if transcript_available:
            reason_parts.append("captions/transcript available")
        if practice_boost > 0:
            reason_parts.append(str(domain_signals["reason_phrase"]))
        if long_form_boost > 0:
            reason_parts.append(f"in-depth {int(duration_minutes)}+ min content")

        return {
            "score": normalized_score,
            "final_score": final_score,
            "breakdown": breakdown,
            "recommended_action": recommended_action,
            "duration_advice": duration_advice,
            "components": {
                "relevance": round(relevance_signal / 100.0, 4),
                "duration_fit": round(max(0.0, 100.0 - duration_penalty) / 100.0, 4),
                "engagement_quality": round(engagement_quality / 100.0, 4),
                "recency": round(recency / 100.0, 4),
                "performance_score": round(performance_adj / 100.0, 4),
                "transcript_score": round(transcript_score / 25.0, 4) if transcript_score else 0.0,
                str(domain_signals["boost_component"]): round(practice_boost / 15.0, 4) if practice_boost else 0.0,
                "channel_age_score": round(channel_age_score / 10.0, 4) if channel_age_score else 0.0,
            },
            "why_this_video": ", ".join(reason_parts),
        }


scoring_engine = ScoringEngine()
