"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-02 22:02:46.926871
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this migration."""
    op.create_table(
        "company",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company")),
        sa.UniqueConstraint("name", name=op.f("uq_company_name")),
    )
    op.create_table(
        "profile_snapshot",
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("resume", sa.Text(), nullable=False),
        sa.Column("criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_profile_snapshot")),
    )
    op.create_table(
        "source",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_source_company_id_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source")),
    )
    op.create_table(
        "scrape_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_scrape_run_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scrape_run")),
    )
    op.create_index(
        "ix_scrape_run_source_id_started_at",
        "scrape_run",
        ["source_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "vacancy",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), server_default="new", nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["company.id"], name=op.f("fk_vacancy_company_id_company")
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["source.id"], name=op.f("fk_vacancy_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vacancy")),
        sa.UniqueConstraint("company_id", "site_id", name=op.f("uq_vacancy_company_id_site_id")),
    )
    op.create_table(
        "application_pack",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("tailored_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancy.id"], name=op.f("fk_application_pack_vacancy_id_vacancy")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_pack")),
    )
    op.create_table(
        "assessment",
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("strengths", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("gaps", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("exclusions", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancy.id"], name=op.f("fk_assessment_vacancy_id_vacancy")
        ),
        sa.PrimaryKeyConstraint("vacancy_id", name=op.f("pk_assessment")),
    )
    op.create_table(
        "prefilter_rejection",
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("filtered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancy.id"], name=op.f("fk_prefilter_rejection_vacancy_id_vacancy")
        ),
        sa.PrimaryKeyConstraint("vacancy_id", name=op.f("pk_prefilter_rejection")),
    )
    op.create_table(
        "revision",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["scrape_run.id"], name=op.f("fk_revision_run_id_scrape_run")
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"], ["vacancy.id"], name=op.f("fk_revision_vacancy_id_vacancy")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revision")),
    )
    op.create_table(
        "run_listing",
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["scrape_run.id"], name=op.f("fk_run_listing_run_id_scrape_run")
        ),
        sa.PrimaryKeyConstraint("run_id", "site_id", name=op.f("pk_run_listing")),
    )


def downgrade() -> None:
    """Revert this migration."""
    op.drop_table("run_listing")
    op.drop_table("revision")
    op.drop_table("prefilter_rejection")
    op.drop_table("assessment")
    op.drop_table("application_pack")
    op.drop_table("vacancy")
    op.drop_index("ix_scrape_run_source_id_started_at", table_name="scrape_run")
    op.drop_table("scrape_run")
    op.drop_table("source")
    op.drop_table("profile_snapshot")
    op.drop_table("company")
