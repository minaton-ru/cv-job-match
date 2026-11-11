"""Alembic environment: runs the migrations online against PostgreSQL through asyncpg."""

import asyncio
import logging

from alembic import context
from sqlalchemy.engine import Connection

from cv_job_match.db.postgres import create_database_engine, read_database_url
from cv_job_match.db.schema import metadata

logger = logging.getLogger(__name__)


def apply_migrations(connection: Connection) -> None:
    """Run the pending migrations on one connection.

    Args:
        connection: A synchronous view of the async connection.
    """
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(database_url: str) -> None:
    """Connect to the database and run the pending migrations.

    Args:
        database_url: SQLAlchemy URL of the database.
    """
    engine = create_database_engine(database_url)
    async with engine.connect() as connection:
        await connection.run_sync(apply_migrations)
    await engine.dispose()


database_url = context.config.attributes.get("database_url") or read_database_url()
logger.info("Running migrations")
asyncio.run(run_async_migrations(database_url))
