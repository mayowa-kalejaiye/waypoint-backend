from __future__ import annotations

from typing import Any

from backend.domain_configs import DOMAIN_CONFIGS


TECH_KEYWORDS = [
    "web app",
    "website",
    "api",
    "database",
    "react",
    "node",
    "python",
    "javascript",
    "html",
    "css",
    "code",
    "programming",
    "software",
    "devops",
    "deploy",
    "github",
    "docker",
    "kubernetes",
    "sql",
    "nosql",
    "frontend",
    "backend",
    "fullstack",
    "algorithm",
    "data structure",
]

CREATIVE_KEYWORDS = [
    "garden",
    "plant",
    "cook",
    "recipe",
    "paint",
    "draw",
    "photograph",
    "music",
    "instrument",
    "write",
    "creative writing",
    "poetry",
    "craft",
    "sew",
    "woodwork",
    "pottery",
    "sketch",
]

BUSINESS_KEYWORDS = [
    "presentation",
    "public speaking",
    "negotiation",
    "leadership",
    "management",
    "sales",
    "interview",
    "resume",
    "career",
    "meeting",
    "team",
    "project management",
    "agile",
    "scrum",
    "conflict resolution",
]

HEALTH_KEYWORDS = [
    "meditation",
    "yoga",
    "fitness",
    "workout",
    "exercise",
    "nutrition",
    "meal",
    "diet",
    "sleep",
    "mental health",
    "anxiety",
    "stress",
    "mindfulness",
    "breathing",
]

ACADEMIC_KEYWORDS = [
    "history",
    "philosophy",
    "physics",
    "chemistry",
    "biology",
    "economics",
    "literature",
    "poetry analysis",
    "art history",
    "music theory",
    "calculus",
    "algebra",
    "geometry",
    "statistics",
]

LIFE_SKILLS_KEYWORDS = [
    "budget",
    "finance",
    "tax",
    "resume",
    "interview job",
    "tire",
    "plumbing",
    "electrical",
    "parenting",
    "time management",
    "organization",
    "cleaning",
    "laundry",
]

HUMANITIES_KEYWORDS = [
    "art history",
    "music theory",
    "film analysis",
    "creative writing",
    "poetry",
    "literary analysis",
    "philosophy",
]


def classify_domain(prompt: str) -> str:
    """
    Returns one of:
    - "tech_development"
    - "creative_arts"
    - "business_professional"
    - "health_wellness"
    - "academic_learning"
    - "life_skills"
    - "humanities"
    """

    prompt_lower = prompt.lower()

    if any(keyword in prompt_lower for keyword in TECH_KEYWORDS):
        return "tech_development"
    if any(keyword in prompt_lower for keyword in CREATIVE_KEYWORDS):
        return "creative_arts"
    if any(keyword in prompt_lower for keyword in BUSINESS_KEYWORDS):
        return "business_professional"
    if any(keyword in prompt_lower for keyword in HEALTH_KEYWORDS):
        return "health_wellness"
    if any(keyword in prompt_lower for keyword in ACADEMIC_KEYWORDS):
        return "academic_learning"
    if any(keyword in prompt_lower for keyword in LIFE_SKILLS_KEYWORDS):
        return "life_skills"
    if any(keyword in prompt_lower for keyword in HUMANITIES_KEYWORDS):
        return "humanities"

    return "humanities"


def get_domain_profile(domain: str) -> dict[str, Any]:
    domain_name = domain.lower().strip()
    profile = dict(DOMAIN_CONFIGS.get(domain_name, DOMAIN_CONFIGS["humanities"]))
    profile.setdefault("resource_types", ["official_docs"])
    profile.setdefault("project_verb", "create")
    profile.setdefault("project_action", "create or practice")
    profile.setdefault("success_criteria", ["The task is completed.", "You can explain the result."])
    profile.setdefault("graph_style", "Use a concrete prerequisite progression.")
    profile.setdefault("search_verbs", ["tutorial", "guide", "example"])
    return profile
