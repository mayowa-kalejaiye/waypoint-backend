from sqlmodel import Session, select

from backend.models.moat_features import ProjectSuggestion
from backend.db.database import fallback_engine

SUGGESTIONS = [
    {
        "concept": "Python",
        "difficulty_level": "beginner",
        "name": "Build a CLI TODO app",
        "description": "Create a command-line TODO application in Python using argparse and file-based storage.",
        "estimated_hours": 3,
        "skills_practiced": ["Python", "CLI", "File I/O"],
        "github_url": "https://github.com/example/python-todo-starter",
    },
    {
        "concept": "React",
        "difficulty_level": "beginner",
        "name": "React Todo App",
        "description": "Build a React TODO app with local state and basic CRUD operations.",
        "estimated_hours": 4,
        "skills_practiced": ["React", "JavaScript", "Frontend"],
        "github_url": "https://github.com/example/react-todo-starter",
    },
    {
        "concept": "FastAPI",
        "difficulty_level": "beginner",
        "name": "Simple REST API",
        "description": "Implement a RESTful API using FastAPI with endpoints for CRUD operations and simple auth.",
        "estimated_hours": 5,
        "skills_practiced": ["Python", "FastAPI", "REST"],
        "github_url": "https://github.com/example/fastapi-starter",
    },
]


def seed():
    # Create only the table we need for seeding.
    try:
        ProjectSuggestion.__table__.create(fallback_engine, checkfirst=True)
    except Exception:
        pass

    with Session(fallback_engine) as session:
        existing = session.exec(select(ProjectSuggestion)).all()
        if existing:
            print(f"ProjectSuggestion table already has {len(existing)} entries; skipping seeding.")
            return
        rows = [
            {
                "concept": item["concept"],
                "difficulty_level": item["difficulty_level"],
                "project_name": item["name"],
                "project_description": item["description"],
                "github_repo_url": item["github_url"],
                "estimated_hours": item["estimated_hours"],
                "skills_practiced": item["skills_practiced"],
                "prerequisites": [],
            }
            for item in SUGGESTIONS
        ]
        session.execute(ProjectSuggestion.__table__.insert(), rows)
        session.commit()
        print("Seeded ProjectSuggestion entries.")


if __name__ == "__main__":
    seed()
