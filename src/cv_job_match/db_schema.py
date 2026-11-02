"""PostgreSQL schema of every stored entity, shared by the repositories and migrations."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from cv_job_match.constants import DB_NAMING_CONVENTION

metadata = MetaData(naming_convention=DB_NAMING_CONVENTION)

company_table = Table(
    "company",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
)

source_table = Table(
    "source",
    metadata,
    Column("id", Text, primary_key=True),
    Column("company_id", ForeignKey("company.id"), nullable=False),
)

vacancy_table = Table(
    "vacancy",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("company_id", ForeignKey("company.id"), nullable=False),
    Column("site_id", Text, nullable=False),
    Column("source_id", ForeignKey("source.id"), nullable=False),
    Column("fields", JSONB, nullable=False),
    Column("description", Text),
    Column("is_baseline", Boolean, nullable=False),
    Column("is_closed", Boolean, nullable=False, server_default=false()),
    Column("status", Text, nullable=False, server_default="new"),
    UniqueConstraint("company_id", "site_id"),
)

scrape_run_table = Table(
    "scrape_run",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", ForeignKey("source.id"), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("status", Text, nullable=False),
    Column("error", Text),
    Index("ix_scrape_run_source_id_started_at", "source_id", "started_at"),
)

run_listing_table = Table(
    "run_listing",
    metadata,
    Column("run_id", ForeignKey("scrape_run.id"), primary_key=True),
    Column("site_id", Text, primary_key=True),
)

revision_table = Table(
    "revision",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("vacancy_id", ForeignKey("vacancy.id"), nullable=False),
    Column("run_id", ForeignKey("scrape_run.id"), nullable=False),
    Column("fields", JSONB, nullable=False),
)

profile_snapshot_table = Table(
    "profile_snapshot",
    metadata,
    Column("version", Text, primary_key=True),
    Column("resume", Text, nullable=False),
    Column("criteria", JSONB, nullable=False),
)

assessment_table = Table(
    "assessment",
    metadata,
    Column("vacancy_id", ForeignKey("vacancy.id"), primary_key=True),
    Column("score", Integer, nullable=False),
    Column("verdict", Text, nullable=False),
    Column("strengths", ARRAY(Text), nullable=False),
    Column("gaps", ARRAY(Text), nullable=False),
    Column("exclusions", ARRAY(Text), nullable=False),
    Column("profile_version", Text, nullable=False),
    Column("assessed_at", DateTime(timezone=True), nullable=False),
)

prefilter_rejection_table = Table(
    "prefilter_rejection",
    metadata,
    Column("vacancy_id", ForeignKey("vacancy.id"), primary_key=True),
    Column("reason", Text, nullable=False),
    Column("profile_version", Text, nullable=False),
    Column("filtered_at", DateTime(timezone=True), nullable=False),
)

application_pack_table = Table(
    "application_pack",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("vacancy_id", ForeignKey("vacancy.id"), nullable=False),
    Column("profile_version", Text, nullable=False),
    Column("language", Text, nullable=False),
    Column("tailored_at", DateTime(timezone=True), nullable=False),
)
