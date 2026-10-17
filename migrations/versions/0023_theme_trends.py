"""Add trending-theme taxonomy, membership, news attention and theme publication tables.

Revision ID: 0023_theme_trends
Revises: 0022_index_valuation_backfill_checkpoints
"""

from alembic import op
import sqlalchemy as sa


revision = "0023_theme_trends"
down_revision = "0022_index_valuation_backfill_checkpoints"
branch_labels = None
depends_on = None

# Parents first. Fresh installs already get these tables from 0001's
# create_all, so every block is guarded and must mirror models.py exactly
# (tests/test_theme_migration.py compares both schemas).
THEME_TABLES = (
    "themes",
    "theme_members",
    "theme_member_verifications",
    "etf_holdings_snapshots",
    "news_ingest_days",
    "news_items",
    "ticker_attention_daily",
    "theme_daily",
    "theme_member_daily",
    "company_trend_daily",
    "theme_briefs",
    "theme_alerts",
    "theme_proposals",
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("themes"):
        op.create_table(
            "themes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name_en", sa.String(), nullable=False),
            sa.Column("name_zh", sa.String(), nullable=False),
            sa.Column("megatrend", sa.String(), nullable=True),
            sa.Column("definition", sa.Text(), nullable=False),
            sa.Column("keywords", sa.JSON(), nullable=False),
            sa.Column("negative_keywords", sa.JSON(), nullable=False),
            sa.Column("proxy_etfs", sa.JSON(), nullable=False),
            sa.Column("seed_core", sa.JSON(), nullable=False),
            sa.Column("pins", sa.JSON(), nullable=False),
            sa.Column("bans", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("taxonomy_version", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("slug", name="uix_themes_slug"),
        )
        op.create_index("ix_themes_status", "themes", ["status"], unique=False)
    if not inspector.has_table("theme_members"):
        op.create_table(
            "theme_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("tier", sa.String(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("exposure", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(), nullable=True),
            sa.Column("sources", sa.JSON(), nullable=False),
            sa.Column("evidence_quote", sa.Text(), nullable=True),
            sa.Column("evidence_source", sa.String(), nullable=True),
            sa.Column("evidence_as_of", sa.Date(), nullable=True),
            sa.Column("llm_model", sa.String(), nullable=True),
            sa.Column("prompt_version", sa.String(), nullable=True),
            sa.Column("review_status", sa.String(), nullable=False),
            sa.Column("valid_from", sa.Date(), nullable=False),
            sa.Column("valid_to", sa.Date(), nullable=True),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("theme_id", "ticker", "valid_from", name="uix_theme_members_theme_ticker_valid_from"),
        )
        op.create_index("ix_theme_members_theme_id", "theme_members", ["theme_id"], unique=False)
        op.create_index("ix_theme_members_theme_interval", "theme_members", ["theme_id", "valid_from", "valid_to"], unique=False)
        op.create_index("ix_theme_members_ticker", "theme_members", ["ticker"], unique=False)
        op.create_index("ix_theme_members_valid_from", "theme_members", ["valid_from"], unique=False)
        op.create_index("ix_theme_members_valid_to", "theme_members", ["valid_to"], unique=False)
    if not inspector.has_table("theme_member_verifications"):
        op.create_table(
            "theme_member_verifications",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("input_hash", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("prompt_version", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("exposure", sa.Integer(), nullable=True),
            sa.Column("role", sa.String(), nullable=True),
            sa.Column("evidence_quote", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("theme_id", "ticker", "input_hash", name="uix_theme_member_verifications_key"),
        )
        op.create_index("ix_theme_member_verifications_theme_id", "theme_member_verifications", ["theme_id"], unique=False)
        op.create_index("ix_theme_member_verifications_ticker", "theme_member_verifications", ["ticker"], unique=False)
    if not inspector.has_table("etf_holdings_snapshots"):
        op.create_table(
            "etf_holdings_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("etf", sa.String(), nullable=False),
            sa.Column("as_of_date", sa.Date(), nullable=False),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("holding_key", sa.String(), nullable=False),
            sa.Column("holding_ticker", sa.String(), nullable=True),
            sa.Column("holding_name", sa.String(), nullable=True),
            sa.Column("exchange", sa.String(), nullable=True),
            sa.Column("weight_pct", sa.Float(), nullable=True),
            sa.Column("inception_date", sa.Date(), nullable=True),
            sa.Column("raw_snapshot_id", sa.Integer(), sa.ForeignKey("raw_data_snapshots.id"), nullable=True),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("etf", "as_of_date", "source", "holding_key", name="uix_etf_holdings_etf_date_source_key"),
        )
        op.create_index("ix_etf_holdings_snapshots_as_of_date", "etf_holdings_snapshots", ["as_of_date"], unique=False)
        op.create_index("ix_etf_holdings_snapshots_etf", "etf_holdings_snapshots", ["etf"], unique=False)
        op.create_index("ix_etf_holdings_snapshots_holding_ticker", "etf_holdings_snapshots", ["holding_ticker"], unique=False)
    if not inspector.has_table("news_ingest_days"):
        op.create_table(
            "news_ingest_days",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("pages", sa.Integer(), nullable=False),
            sa.Column("articles_total", sa.Integer(), nullable=False),
            sa.Column("articles_us", sa.Integer(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False),
            sa.Column("raw_snapshot_id", sa.Integer(), sa.ForeignKey("raw_data_snapshots.id"), nullable=True),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("day", name="uix_news_ingest_days_day"),
        )
        op.create_index("ix_news_ingest_days_status", "news_ingest_days", ["status"], unique=False)
    if not inspector.has_table("news_items"):
        op.create_table(
            "news_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url_hash", sa.String(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("published_at", sa.DateTime(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("link", sa.String(), nullable=True),
            sa.Column("source_domain", sa.String(), nullable=True),
            sa.Column("tickers", sa.JSON(), nullable=False),
            sa.Column("symbol_count", sa.Integer(), nullable=False),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("raw_snapshot_id", sa.Integer(), sa.ForeignKey("raw_data_snapshots.id"), nullable=True),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("url_hash", name="uix_news_items_url_hash"),
        )
        op.create_index("ix_news_items_day", "news_items", ["day"], unique=False)
        op.create_index("ix_news_items_published_at", "news_items", ["published_at"], unique=False)
    if not inspector.has_table("ticker_attention_daily"):
        op.create_table(
            "ticker_attention_daily",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("article_count", sa.Integer(), nullable=False),
            sa.Column("focused_count", sa.Integer(), nullable=False),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=True),
            sa.UniqueConstraint("ticker", "day", name="uix_ticker_attention_daily_ticker_day"),
        )
        op.create_index("ix_ticker_attention_daily_day", "ticker_attention_daily", ["day"], unique=False)
        op.create_index("ix_ticker_attention_daily_ticker", "ticker_attention_daily", ["ticker"], unique=False)
    if not inspector.has_table("theme_daily"):
        op.create_table(
            "theme_daily",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("scoring_version", sa.String(), nullable=False),
            sa.Column("pit", sa.Boolean(), nullable=False),
            sa.Column("rankable", sa.Boolean(), nullable=False),
            sa.Column("n_members", sa.Integer(), nullable=False),
            sa.Column("n_priced", sa.Integer(), nullable=False),
            sa.Column("ret_1d", sa.Float(), nullable=True),
            sa.Column("basket_index", sa.Float(), nullable=True),
            sa.Column("x5", sa.Float(), nullable=True),
            sa.Column("x21", sa.Float(), nullable=True),
            sa.Column("x63", sa.Float(), nullable=True),
            sa.Column("x126", sa.Float(), nullable=True),
            sa.Column("b50", sa.Float(), nullable=True),
            sa.Column("nh", sa.Float(), nullable=True),
            sa.Column("d_b50", sa.Float(), nullable=True),
            sa.Column("ign", sa.Integer(), nullable=False),
            sa.Column("ign5", sa.Integer(), nullable=False),
            sa.Column("rvol5_median", sa.Float(), nullable=True),
            sa.Column("att", sa.Float(), nullable=True),
            sa.Column("att_breadth", sa.Float(), nullable=True),
            sa.Column("score_price", sa.Float(), nullable=True),
            sa.Column("score_breadth", sa.Float(), nullable=True),
            sa.Column("score_volume", sa.Float(), nullable=True),
            sa.Column("score_attention", sa.Float(), nullable=True),
            sa.Column("h_raw", sa.Float(), nullable=True),
            sa.Column("heat", sa.Float(), nullable=True),
            sa.Column("heat_now", sa.Float(), nullable=True),
            sa.Column("heat_trend", sa.Float(), nullable=True),
            sa.Column("pct_now", sa.Float(), nullable=True),
            sa.Column("pct_trend", sa.Float(), nullable=True),
            sa.Column("rank_now", sa.Integer(), nullable=True),
            sa.Column("rank_trend", sa.Integer(), nullable=True),
            sa.Column("rank_change", sa.Integer(), nullable=True),
            sa.Column("streak_top5", sa.Integer(), nullable=False),
            sa.Column("ts_z", sa.Float(), nullable=True),
            sa.Column("cocorr", sa.Float(), nullable=True),
            sa.Column("cocorr_pct", sa.Float(), nullable=True),
            sa.Column("emerge", sa.Boolean(), nullable=False),
            sa.Column("attention_only", sa.Boolean(), nullable=False),
            sa.Column("attention_degraded", sa.Boolean(), nullable=False),
            sa.Column("candidate_stage", sa.String(), nullable=False),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("stage_prev", sa.String(), nullable=True),
            sa.Column("stage_changed", sa.Boolean(), nullable=False),
            sa.Column("emerging_first_20", sa.Boolean(), nullable=False),
            sa.Column("proxy_etf", sa.String(), nullable=True),
            sa.Column("proxy_x21", sa.Float(), nullable=True),
            sa.Column("leaders", sa.JSON(), nullable=True),
            sa.Column("flags", sa.JSON(), nullable=True),
        )
        op.create_index("ix_theme_daily_date", "theme_daily", ["date"], unique=False)
        op.create_index("ix_theme_daily_run_theme_date", "theme_daily", ["source_run_id", "theme_id", "date"], unique=True)
        op.create_index("ix_theme_daily_stage", "theme_daily", ["stage"], unique=False)
        op.create_index("ix_theme_daily_theme_date", "theme_daily", ["theme_id", "date"], unique=False)
    if not inspector.has_table("theme_member_daily"):
        op.create_table(
            "theme_member_daily",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("tier", sa.String(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("close", sa.Float(), nullable=True),
            sa.Column("r1", sa.Float(), nullable=True),
            sa.Column("x5", sa.Float(), nullable=True),
            sa.Column("x21", sa.Float(), nullable=True),
            sa.Column("pth", sa.Float(), nullable=True),
            sa.Column("rvol5", sa.Float(), nullable=True),
            sa.Column("att_z", sa.Float(), nullable=True),
            sa.Column("ignited", sa.Boolean(), nullable=False),
            sa.Column("first_ignition_date", sa.Date(), nullable=True),
            sa.Column("leader_score", sa.Float(), nullable=True),
            sa.Column("leader_rank", sa.Integer(), nullable=True),
            sa.Column("is_leader", sa.Boolean(), nullable=False),
            sa.Column("sparkline", sa.JSON(), nullable=True),
        )
        op.create_index("ix_theme_member_daily_date", "theme_member_daily", ["date"], unique=False)
        op.create_index("ix_theme_member_daily_run_theme_ticker_date", "theme_member_daily", ["source_run_id", "theme_id", "ticker", "date"], unique=True)
        op.create_index("ix_theme_member_daily_ticker", "theme_member_daily", ["ticker"], unique=False)
    if not inspector.has_table("company_trend_daily"):
        op.create_table(
            "company_trend_daily",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("sector", sa.String(), nullable=True),
            sa.Column("industry", sa.String(), nullable=True),
            sa.Column("close", sa.Float(), nullable=True),
            sa.Column("market_cap", sa.Float(), nullable=True),
            sa.Column("r1", sa.Float(), nullable=True),
            sa.Column("x5", sa.Float(), nullable=True),
            sa.Column("x5_sector", sa.Float(), nullable=True),
            sa.Column("rvol5", sa.Float(), nullable=True),
            sa.Column("att_z", sa.Float(), nullable=True),
            sa.Column("pth", sa.Float(), nullable=True),
            sa.Column("theme_slugs", sa.JSON(), nullable=True),
            sa.Column("flags", sa.JSON(), nullable=True),
        )
        op.create_index("ix_company_trend_daily_date", "company_trend_daily", ["date"], unique=False)
        op.create_index("ix_company_trend_daily_run_ticker_date", "company_trend_daily", ["source_run_id", "ticker", "date"], unique=True)
        op.create_index("ix_company_trend_daily_ticker", "company_trend_daily", ["ticker"], unique=False)
    if not inspector.has_table("theme_briefs"):
        op.create_table(
            "theme_briefs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("evidence_hash", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("prompt_version", sa.String(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("validation_report", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("theme_id", "evidence_hash", "model", name="uix_theme_briefs_evidence_model"),
        )
        op.create_index("ix_theme_briefs_date", "theme_briefs", ["date"], unique=False)
        op.create_index("ix_theme_briefs_evidence_hash", "theme_briefs", ["evidence_hash"], unique=False)
        op.create_index("ix_theme_briefs_theme_id", "theme_briefs", ["theme_id"], unique=False)
    if not inspector.has_table("theme_alerts"):
        op.create_table(
            "theme_alerts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("theme_id", sa.Integer(), sa.ForeignKey("themes.id"), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("alert_type", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("notified_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("theme_id", "alert_type", "date", name="uix_theme_alerts_theme_type_date"),
        )
        op.create_index("ix_theme_alerts_date", "theme_alerts", ["date"], unique=False)
        op.create_index("ix_theme_alerts_theme_id", "theme_alerts", ["theme_id"], unique=False)
    if not inspector.has_table("theme_proposals"):
        op.create_table(
            "theme_proposals",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("proposal_key", sa.String(), nullable=False),
            sa.Column("label_en", sa.String(), nullable=True),
            sa.Column("label_zh", sa.String(), nullable=True),
            sa.Column("slug_suggestion", sa.String(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("tickers", sa.JSON(), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=True),
            sa.Column("first_seen", sa.Date(), nullable=False),
            sa.Column("last_seen", sa.Date(), nullable=False),
            sa.Column("hit_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("llm_model", sa.String(), nullable=True),
            sa.Column("prompt_version", sa.String(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("proposal_key", name="uix_theme_proposals_key"),
        )
        op.create_index("ix_theme_proposals_last_seen", "theme_proposals", ["last_seen"], unique=False)
        op.create_index("ix_theme_proposals_status", "theme_proposals", ["status"], unique=False)
    op.execute("PRAGMA optimize")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in reversed(THEME_TABLES):
        if inspector.has_table(table):
            op.drop_table(table)
