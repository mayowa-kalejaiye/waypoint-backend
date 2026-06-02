from __future__ import annotations

from typing import Any

from backend.services.domain_classifier import classify_domain


NON_TECH_DOMAINS = {
    "creative_arts",
    "business_professional",
    "health_wellness",
    "academic_learning",
    "life_skills",
    "humanities",
}

TECH_TERMS = [
    "github",
    "repository",
    "repositories",
    "code",
    "deploy",
    "npm",
    "docker",
    "api",
    "build a",
    "compile",
    "push",
    "commit",
    "pull request",
]

TECH_RESOURCE_TYPES = {"github", "stack_overflow", "npm", "docker_hub"}


def validate_diversity_fit(user_prompt: str, generated_curriculum: dict[str, Any]) -> dict[str, Any]:
    domain = classify_domain(user_prompt)
    if domain not in NON_TECH_DOMAINS:
        return generated_curriculum

    warnings: list[str] = []

    dependency_graph = generated_curriculum.get("dependency_graph", {})
    nodes = dependency_graph.get("nodes", []) if isinstance(dependency_graph, dict) else []
    for node in nodes:
        node_name = str(node.get("name", "")).lower()
        if any(term in node_name for term in TECH_TERMS):
            warnings.append(f"Node '{node.get('name', '')}' contains tech term for {domain} domain")

    projects = generated_curriculum.get("projects", {})
    daily_breakdown = projects.get("daily_breakdown", []) if isinstance(projects, dict) else []
    for project in daily_breakdown:
        project_title = str(project.get("project_title", ""))
        project_desc = str(project.get("description", "")).lower()
        if any(term in project_desc for term in TECH_TERMS):
            warnings.append(f"Project '{project_title}' uses tech terminology")

    resource_strategy = generated_curriculum.get("resource_strategy", {})
    per_node = resource_strategy.get("per_node", []) if isinstance(resource_strategy, dict) else []
    for node_resources in per_node:
        for resource_type in node_resources.get("resource_types", []) or []:
            resource_name = str(resource_type).strip().lower()
            if resource_name in TECH_RESOURCE_TYPES:
                warnings.append(f"Resource type '{resource_name}' is tech-specific for {domain} domain")

    if warnings:
        generated_curriculum["diversity_warnings"] = warnings
    if len(warnings) > 2:
        generated_curriculum["requires_review"] = True

    return generated_curriculum
