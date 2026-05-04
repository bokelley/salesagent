"""Database module for Prebid Sales Agent Server.

This module contains all database-related functionality including:
- Database configuration and connection management
- Database schema definitions and migrations
- SQLAlchemy models and ORM mappings
- Database session handling and context management

Key components:
- db_config.py: Database configuration and connection setup
- database.py: Core database utilities and initialization
- database_schema.py: Schema definitions and table creation
- database_session.py: Session management and context handlers
- models.py: SQLAlchemy ORM models for all entities
- embedded_tenant_guard.py: model-layer write guard for platform-managed surfaces
"""

# Importing the guard registers SQLAlchemy event listeners as a side effect.
# Keep this import at module load time so listeners are always attached.
from src.core.database import embedded_tenant_guard as _embedded_tenant_guard  # noqa: F401
