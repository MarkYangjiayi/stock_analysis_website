"""Load the Git-managed theme taxonomy and sync it into the ``themes`` table."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from core.config import settings
from database import async_session_maker
from models import Theme
from services.security_master import canonicalize_ticker

logger = logging.getLogger(__name__)

DEFAULT_TAXONOMY_PATH = Path(__file__).resolve().parent / "taxonomy" / "themes_v1.yaml"
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ThemeStatus = Literal["active", "watch", "retired"]


def _canonical_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(canonicalize_ticker(value) for value in values if str(value).strip()))


class ThemeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    name_en: str
    name_zh: str
    megatrend: Optional[str] = None
    definition: str
    keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    proxy_etfs: list[str] = Field(default_factory=list)
    seed_core: list[str] = Field(default_factory=list)
    pins: list[str] = Field(default_factory=list)
    bans: list[str] = Field(default_factory=list)
    status: ThemeStatus = "active"

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError(f"invalid theme slug: {value!r}")
        return value

    @field_validator("name_en", "name_zh", "definition")
    @classmethod
    def _required_text(cls, value: str) -> str:
        cleaned = " ".join(str(value).split())
        if not cleaned:
            raise ValueError("theme text fields must not be empty")
        return cleaned

    @field_validator("keywords", "negative_keywords")
    @classmethod
    def _phrases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(" ".join(str(v).lower().split()) for v in values if str(v).strip()))

    @field_validator("proxy_etfs", "seed_core", "pins", "bans")
    @classmethod
    def _tickers(cls, values: list[str]) -> list[str]:
        return _canonical_list(values)

    @model_validator(mode="after")
    def _overrides_disjoint(self) -> "ThemeDefinition":
        overlap = set(self.pins) & set(self.bans)
        if overlap:
            raise ValueError(f"{self.slug}: tickers both pinned and banned: {sorted(overlap)}")
        return self


class Taxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    themes: list[ThemeDefinition]

    @model_validator(mode="after")
    def _unique_slugs(self) -> "Taxonomy":
        slugs = [theme.slug for theme in self.themes]
        duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
        if duplicates:
            raise ValueError(f"duplicate theme slugs: {duplicates}")
        return self

    def active(self) -> list[ThemeDefinition]:
        return [theme for theme in self.themes if theme.status != "retired"]

    def by_slug(self) -> dict[str, ThemeDefinition]:
        return {theme.slug: theme for theme in self.themes}

    def proxy_etfs(self) -> list[str]:
        """All proxy ETFs of non-retired themes, canonical and de-duplicated."""
        return list(dict.fromkeys(etf for theme in self.active() for etf in theme.proxy_etfs))


def taxonomy_path() -> Path:
    configured = (settings.THEME_TAXONOMY_PATH or "").strip()
    return Path(configured) if configured else DEFAULT_TAXONOMY_PATH


def parse_taxonomy(text: str) -> Taxonomy:
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError("theme taxonomy must be a mapping")
    return Taxonomy.model_validate(payload)


@lru_cache(maxsize=4)
def _load_cached(path: str, mtime_ns: int) -> Taxonomy:
    return parse_taxonomy(Path(path).read_text(encoding="utf-8"))


def load_taxonomy(path: Optional[Path] = None) -> Taxonomy:
    """Parse and validate the taxonomy file (cached per path and mtime)."""
    target = Path(path) if path is not None else taxonomy_path()
    return _load_cached(str(target.resolve()), target.stat().st_mtime_ns)


async def sync_theme_taxonomy(taxonomy: Optional[Taxonomy] = None) -> dict:
    """Upsert every taxonomy theme into ``themes``; themes absent from the file are retired.

    Rows are never deleted because membership and daily rows reference them.
    """
    taxonomy = taxonomy or load_taxonomy()
    seen = set()
    created = updated = retired = 0
    async with async_session_maker() as db, db.begin():
        existing = {row.slug: row for row in (await db.execute(select(Theme))).scalars()}
        for definition in taxonomy.themes:
            seen.add(definition.slug)
            values = {
                "name_en": definition.name_en,
                "name_zh": definition.name_zh,
                "megatrend": definition.megatrend,
                "definition": definition.definition,
                "keywords": list(definition.keywords),
                "negative_keywords": list(definition.negative_keywords),
                "proxy_etfs": list(definition.proxy_etfs),
                "seed_core": list(definition.seed_core),
                "pins": list(definition.pins),
                "bans": list(definition.bans),
                "status": definition.status,
                "taxonomy_version": taxonomy.version,
            }
            row = existing.get(definition.slug)
            if row is None:
                db.add(Theme(slug=definition.slug, **values))
                created += 1
            elif any(getattr(row, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(row, key, value)
                updated += 1
        for slug, row in existing.items():
            if slug not in seen and row.status != "retired":
                row.status = "retired"
                retired += 1
    result = {"version": taxonomy.version, "themes": len(taxonomy.themes),
              "created": created, "updated": updated, "retired": retired}
    logger.info("Theme taxonomy synced: %s", result)
    return result
