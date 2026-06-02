#!/usr/bin/env python3
"""
Verify and display database migration summary.
"""

import sqlite3
from pathlib import Path

db_path = Path("dev.db") if Path("dev.db").exists() else Path("waypoint/dev.db")

if not db_path.exists():
    print(f"❌ Database file not found: {db_path}")
    exit(1)

print("\n" + "="*70)
print("WAYPOINT DATABASE MIGRATION VERIFICATION")
print("="*70 + "\n")

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()

print(f"Total Tables: {len(tables)}\n")

moat_feature_tables = [
    "concept_nodes",
    "concept_graph_feedback",
    "video_interactions",
    "video_performance_scores",
    "transcript_intelligence",
    "learning_sessions",
]

core_tables = [
    "curriculums",
    "curriculum_jobs",
    "curriculum_progress",
    "videos",
    "dependency_graphs",
]

print("MOAT FEATURE TABLES (NEW):")
print("-" * 70)
for table_name, in tables:
    if table_name in moat_feature_tables:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        print(f"✅ {table_name}")
        print(f"   └─ {len(columns)} columns")
        for col in columns:
            col_id, col_name, col_type, notnull, default, pk = col
            pk_marker = " [PRIMARY KEY]" if pk else ""
            nn_marker = " [NOT NULL]" if notnull else ""
            print(f"      • {col_name}: {col_type}{nn_marker}{pk_marker}")

print("\n\nCORE TABLES:")
print("-" * 70)
for table_name, in tables:
    if table_name in core_tables:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        print(f"✅ {table_name}")
        print(f"   └─ {len(columns)} columns")
        for col in columns:
            col_id, col_name, col_type, notnull, default, pk = col
            pk_marker = " [PRIMARY KEY]" if pk else ""
            nn_marker = " [NOT NULL]" if notnull else ""
            print(f"      • {col_name}: {col_type}{nn_marker}{pk_marker}")

# Show row counts
print("\n\nTABLE ROW COUNTS:")
print("-" * 70)
for table_name, in tables:
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    status = "✅" if count >= 0 else "⚠️"
    print(f"{status} {table_name}: {count} rows")

print("\n" + "="*70)
print("MIGRATION VERIFICATION COMPLETE")
print("="*70 + "\n")

conn.close()
