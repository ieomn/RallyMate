"""Portable storage schema; timestamps and job JSON retain their existing text format."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, Table, Text, UniqueConstraint, text


metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False),
    Column("username", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False, server_default="USER"),
    Column("status", Text, nullable=False, server_default="PENDING"),
    Column("application_reason", Text),
    Column("created_at", Text, nullable=False),
    Column("last_login_at", Text),
    Column("deleted_at", Text),
    Column("password_changed_at", Text),
    Column("must_change_password", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("email", name="uq_users_email"),
    CheckConstraint("email = lower(trim(email)) AND email <> ''", name="ck_users_email_normalized"),
    CheckConstraint("role IN ('ADMIN', 'USER')", name="ck_users_role"),
    CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'SUSPENDED')", name="ck_users_status"),
    CheckConstraint("must_change_password IN (0, 1)", name="ck_users_must_change_password"),
)

jobs = Table(
    "jobs", metadata,
    Column("id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("original_filename", Text, nullable=False),
    Column("video_path", Text, nullable=False),
    Column("request_path", Text, nullable=False),
    Column("output_dir", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("started_at", Text),
    Column("completed_at", Text),
    Column("lease_expires_at", Text),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("worker_id", Text),
    Column("progress_json", Text),
    Column("summary_json", Text),
    Column("error", Text),
    Column("cancel_requested", Integer, nullable=False, server_default=text("0")),
    Column("cancel_requested_at", Text),
    Column("cancelled_at", Text),
    Column("user_id", Text, ForeignKey("users.id", name="fk_jobs_user_id_users")),
    Column("deleted_at", Text),
    CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_jobs_status"),
    Index("idx_jobs_status_created", "status", "created_at"),
    Index("idx_jobs_user_created", "user_id", "created_at"),
)

sessions = Table(
    "sessions", metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", name="fk_sessions_user_id_users"), nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("revoked_at", Text),
    Column("csrf_token_hash", Text, nullable=False),
    Index("idx_sessions_user_id", "user_id"),
)

upload_owners = Table(
    "upload_owners", metadata,
    Column("upload_id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", name="fk_upload_owners_user_id_users"), nullable=False),
    Column("created_at", Text, nullable=False),
    Index("idx_upload_owners_user_id", "user_id"),
)

admin_audit = Table(
    "admin_audit", metadata,
    Column("id", Text, primary_key=True),
    Column("actor_id", Text, ForeignKey("users.id", name="fk_admin_audit_actor_id_users"), nullable=False),
    Column("target_user_id", Text, ForeignKey("users.id", name="fk_admin_audit_target_user_id_users")),
    Column("action", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("details_json", Text, nullable=False, server_default="{}"),
)

auth_rate_limits = Table(
    "auth_rate_limits", metadata,
    Column("key", Text, primary_key=True),
    Column("window_start", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("attempts >= 0", name="ck_auth_rate_limits_attempts"),
)
