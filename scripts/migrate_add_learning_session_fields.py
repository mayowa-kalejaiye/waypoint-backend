"""Migration: Add missing moat-feature fields in existing tables."""

from sqlalchemy import text
from backend.db.database import engine

def run_migration():
    """Add missing columns to learning_sessions and concept_nodes tables."""
    with engine.connect() as connection:
        try:
            # Check if columns exist before adding
            connection.execute(text("""
                ALTER TABLE learning_sessions
                ADD COLUMN IF NOT EXISTS viewed_content_ids JSONB DEFAULT '[]'::jsonb;
            """))
            print("✓ Added viewed_content_ids column")
        except Exception as e:
            print(f"✗ viewed_content_ids: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE learning_sessions
                ADD COLUMN IF NOT EXISTS completed_concepts JSONB DEFAULT '[]'::jsonb;
            """))
            print("✓ Added completed_concepts column")
        except Exception as e:
            print(f"✗ completed_concepts: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE learning_sessions
                ADD COLUMN IF NOT EXISTS source_preferences JSONB DEFAULT '{}'::jsonb;
            """))
            print("✓ Added source_preferences column")
        except Exception as e:
            print(f"✗ source_preferences: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE learning_sessions
                ADD COLUMN IF NOT EXISTS learning_pace FLOAT DEFAULT 1.0;
            """))
            print("✓ Added learning_pace column")
        except Exception as e:
            print(f"✗ learning_pace: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE learning_sessions
                ADD COLUMN IF NOT EXISTS preferred_sources JSONB DEFAULT '[]'::jsonb;
            """))
            print("✓ Added preferred_sources column")
        except Exception as e:
            print(f"✗ preferred_sources: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE concept_nodes
                ADD COLUMN IF NOT EXISTS concept_type VARCHAR(50) DEFAULT 'mixed';
            """))
            print("✓ Added concept_type column")
        except Exception as e:
            print(f"✗ concept_type: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE concept_nodes
                ADD COLUMN IF NOT EXISTS estimated_hours DOUBLE PRECISION DEFAULT 1.0;
            """))
            print("✓ Added estimated_hours column")
        except Exception as e:
            print(f"✗ estimated_hours: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE concept_nodes
                ADD COLUMN IF NOT EXISTS times_validated INTEGER DEFAULT 0;
            """))
            print("✓ Added times_validated column")
        except Exception as e:
            print(f"✗ times_validated: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE concept_nodes
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW();
            """))
            print("✓ Added updated_at column")
        except Exception as e:
            print(f"✗ updated_at: {e}")

        connection.commit()
        print("\n✓ Migration completed successfully!")


if __name__ == "__main__":
    run_migration()
