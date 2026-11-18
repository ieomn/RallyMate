"""Add accounts and nullable ownership while preserving legacy job records."""

from alembic import op
import sqlalchemy as sa


revision = "20261117_01_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="USER"),
        sa.Column("status", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("application_reason", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_login_at", sa.Text()),
        sa.Column("deleted_at", sa.Text()),
        sa.Column("password_changed_at", sa.Text()),
        sa.Column("must_change_password", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(trim(email)) AND email <> ''", name="ck_users_email_normalized"),
        sa.CheckConstraint("role IN ('ADMIN', 'USER')", name="ck_users_role"),
        sa.CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'SUSPENDED')", name="ck_users_status"),
        sa.CheckConstraint("must_change_password IN (0, 1)", name="ck_users_must_change_password"),
    )
    bind = op.get_bind()
    existing_jobs = not op.get_context().as_sql and sa.inspect(bind).has_table("jobs")
    if existing_jobs:
        if bind.dialect.name != "sqlite":
            raise RuntimeError("An unversioned PostgreSQL jobs table must be imported into an empty migrated database")
        # Nullable REFERENCES can be added without rebuilding jobs or its dependants.
        op.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT REFERENCES users(id)")
        op.add_column("jobs", sa.Column("deleted_at", sa.Text()))
    else:
        op.create_table(
            "jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("original_filename", sa.Text(), nullable=False),
            sa.Column("video_path", sa.Text(), nullable=False),
            sa.Column("request_path", sa.Text(), nullable=False),
            sa.Column("output_dir", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("started_at", sa.Text()),
            sa.Column("completed_at", sa.Text()),
            sa.Column("lease_expires_at", sa.Text()),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("worker_id", sa.Text()),
            sa.Column("progress_json", sa.Text()),
            sa.Column("summary_json", sa.Text()),
            sa.Column("error", sa.Text()),
            sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("cancel_requested_at", sa.Text()),
            sa.Column("cancelled_at", sa.Text()),
            sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", name="fk_jobs_user_id_users")),
            sa.Column("deleted_at", sa.Text()),
            sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_jobs_status"),
        )
        op.create_index("idx_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_index("idx_jobs_user_created", "jobs", ["user_id", "created_at"])
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", name="fk_sessions_user_id_users"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text()),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
    )
    op.create_index("idx_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "upload_owners",
        sa.Column("upload_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id", name="fk_upload_owners_user_id_users"), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_upload_owners_user_id", "upload_owners", ["user_id"])
    op.create_table(
        "admin_audit",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_id", sa.Text(), sa.ForeignKey("users.id", name="fk_admin_audit_actor_id_users"), nullable=False),
        sa.Column("target_user_id", sa.Text(), sa.ForeignKey("users.id", name="fk_admin_audit_target_user_id_users")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "auth_rate_limits",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("attempts >= 0", name="ck_auth_rate_limits_attempts"),
    )


def downgrade():
    raise RuntimeError("Account schema rollback requires restoring the pre-migration backup")
