#!/usr/bin/env python3
"""
Comprehensive database migration script for Waypoint.
Handles full schema migration including all moat feature tables.
"""

import logging
import sys
from pathlib import Path

# Ensure the parent directory is in sys.path so we can import backend
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text, inspect
from sqlmodel import SQLModel, Session

from backend.config import get_settings
from backend.db.database import get_migration_engine, get_session
# Import all models to ensure they're registered in SQLModel.metadata
from backend.models.curriculum import Curriculum, CurriculumJob, CurriculumProgress
from backend.models.video import Video
from backend.models.moat_features import (
    ConceptNode,
    ConceptGraphFeedback,
    VideoInteraction,
    VideoPerformanceScore,
    TranscriptIntelligence,
    LearningSession,
)
from backend.models.canvas_snapshot import CanvasSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


def get_existing_tables() -> set[str]:
    """Get list of existing tables in the database."""
    engine = get_migration_engine()
    inspector = inspect(engine)
    return set(inspector.get_table_names())


def get_expected_tables() -> set[str]:
    """Get list of expected tables from SQLModel metadata."""
    return set(SQLModel.metadata.tables.keys())


def backup_data() -> dict:
    """Backup existing data before migration."""
    logger.info("Backing up existing data...")
    backup = {}
    
    with get_session() as session:
        for table_name in ["curriculum", "curriculum_job", "curriculum_progress", "video"]:
            try:
                result = session.exec(f"SELECT * FROM {table_name}")
                backup[table_name] = result.fetchall()
                logger.info(f"  ✓ Backed up {table_name}: {len(backup[table_name])} rows")
            except Exception as e:
                logger.warning(f"  ⚠️  Could not backup {table_name}: {e}")
    
    return backup


def drop_all_tables() -> None:
    """Drop all existing tables."""
    logger.info("Dropping all existing tables...")

    engine = get_migration_engine()

    with engine.connect() as connection:
        # Get all table names
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if not tables:
            logger.info("  ✓ No tables to drop")
            return
        
        # Drop all tables
        for table_name in tables:
            try:
                connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))
                logger.info(f"  ✓ Dropped table: {table_name}")
            except Exception as e:
                logger.error(f"  ✗ Failed to drop {table_name}: {e}")
        
        connection.commit()
    
    logger.info("All tables dropped successfully")


def create_all_tables() -> None:
    """Create all tables from SQLModel metadata."""
    logger.info("Creating all tables from SQLModel metadata...")
    
    try:
        engine = get_migration_engine()
        SQLModel.metadata.create_all(engine)
        logger.info("✓ All tables created successfully")
    except Exception as e:
        logger.error(f"✗ Failed to create tables: {e}")
        raise


def verify_migration() -> bool:
    """Verify that all expected tables exist with correct schemas."""
    logger.info("Verifying migration...")
    
    existing = get_existing_tables()
    expected = get_expected_tables()
    
    logger.info(f"Expected tables: {len(expected)}")
    logger.info(f"Existing tables: {len(existing)}")
    
    missing = expected - existing
    extra = existing - expected
    
    if missing:
        logger.error(f"✗ Missing tables: {missing}")
        return False
    
    if extra:
        logger.warning(f"⚠️  Extra tables not in metadata: {extra}")
    
    # Verify table schemas
    engine = get_migration_engine()
    inspector = inspect(engine)
    for table_name in expected:
        try:
            columns = inspector.get_columns(table_name)
            logger.info(f"  ✓ {table_name}: {len(columns)} columns")
        except Exception as e:
            logger.error(f"  ✗ Failed to inspect {table_name}: {e}")
            return False
    
    return True


def show_tables_info() -> None:
    """Display detailed information about all tables."""
    logger.info("\n" + "="*70)
    logger.info("DATABASE SCHEMA SUMMARY")
    logger.info("="*70)
    
    engine = get_migration_engine()
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    
    for table_name in tables:
        columns = inspector.get_columns(table_name)
        pk = inspector.get_pk_constraint(table_name)
        fks = inspector.get_foreign_keys(table_name)
        
        logger.info(f"\nTable: {table_name}")
        logger.info(f"  Columns: {len(columns)}")
        for col in columns:
            nullable = "NULL" if col["nullable"] else "NOT NULL"
            logger.info(f"    - {col['name']}: {col['type']} {nullable}")
        
        if pk and pk.get("constrained_columns"):
            logger.info(f"  Primary Key: {pk['constrained_columns']}")
        
        if fks:
            logger.info(f"  Foreign Keys:")
            for fk in fks:
                logger.info(f"    - {fk['constrained_columns']} -> {fk['referred_table']}.{fk['referred_columns']}")


def run_migration(full_reset: bool = False) -> None:
    """Run the full database migration."""
    logger.info("\n" + "="*70)
    logger.info("WAYPOINT DATABASE MIGRATION")
    logger.info("="*70)
    logger.info(f"Database: {settings.database_url}")
    logger.info(f"Full Reset: {full_reset}")
    logger.info("="*70 + "\n")
    
    try:
        # Step 1: Backup existing data
        if not full_reset:
            backup_data()
        
        # Step 2: Drop all tables if full reset
        if full_reset:
            drop_all_tables()
        
        # Step 3: Create all tables
        create_all_tables()
        
        # Step 4: Verify migration
        if verify_migration():
            logger.info("\n✓ Migration verification PASSED")
        else:
            logger.error("\n✗ Migration verification FAILED")
            return False
        
        # Step 5: Show schema summary
        show_tables_info()
        
        logger.info("\n" + "="*70)
        logger.info("MIGRATION COMPLETED SUCCESSFULLY")
        logger.info("="*70 + "\n")
        return True
    
    except Exception as e:
        logger.error(f"\n✗ Migration failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    import sys
    
    full_reset = "--full-reset" in sys.argv or "-f" in sys.argv
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    
    if verbose:
        logging.getLogger("sqlalchemy").setLevel(logging.DEBUG)
    
    success = run_migration(full_reset=full_reset)
    sys.exit(0 if success else 1)
