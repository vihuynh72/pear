"""All runtime configuration, from environment variables only.

Every knob has a default that works on Cloud Run. Locally, the defaults plus
`gcloud auth application-default login` are enough. `GET /debug/config` prints the
resolved values (secrets redacted). See docs/BACKEND.md for the full table.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent  # .../api
REPO_DIR = API_DIR.parent


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v is not None else default


def _fixtures_dir() -> Path:
    # Repo checkout: <repo>/contracts/fixtures. Container: /app/contracts/fixtures (Dockerfile).
    explicit = _env("PEAR_FIXTURES_DIR")
    if explicit:
        return Path(explicit)
    for p in (REPO_DIR / "contracts" / "fixtures", API_DIR / "contracts" / "fixtures"):
        if p.exists():
            return p
    return REPO_DIR / "contracts" / "fixtures"


@dataclass(frozen=True)
class Settings:
    # --- identity ---
    service: str = field(default_factory=lambda: _env("K_SERVICE", "pear-api"))
    revision: str = field(default_factory=lambda: _env("K_REVISION", "local"))
    version: str = field(default_factory=lambda: _env("PEAR_VERSION", "0.3.0"))
    git_sha: str = field(default_factory=lambda: _env("PEAR_GIT_SHA", "dev"))
    env: str = field(default_factory=lambda: "cloudrun" if _env("K_SERVICE") else "local")

    # --- Google Cloud ---
    project: str = field(
        default_factory=lambda: _env("PEAR_PROJECT", _env("GOOGLE_CLOUD_PROJECT", "pear-510410"))
    )
    region: str = field(default_factory=lambda: _env("PEAR_REGION", "us-central1"))
    # Gemini 3.x is served only from the global location (addendum 5.8).
    gemini_location: str = field(default_factory=lambda: _env("PEAR_GEMINI_LOCATION", "global"))

    # --- models (plan 8.8, addendum 8/9) ---
    model_label: str = field(default_factory=lambda: _env("PEAR_MODEL_LABEL", "gemini-3.5-flash-lite"))
    model_question: str = field(default_factory=lambda: _env("PEAR_MODEL_QUESTION", "gemini-3.5-flash-lite"))
    model_text: str = field(default_factory=lambda: _env("PEAR_MODEL_TEXT", "gemini-3.5-flash-lite"))
    model_price: str = field(default_factory=lambda: _env("PEAR_MODEL_PRICE", "gemini-3.5-flash"))
    model_embedding: str = field(default_factory=lambda: _env("PEAR_MODEL_EMBEDDING", "gemini-embedding-2"))
    model_embedding_fallback: str = field(
        default_factory=lambda: _env("PEAR_MODEL_EMBEDDING_FALLBACK", "gemini-embedding-001")
    )
    embedding_dim: int = field(default_factory=lambda: int(_env("PEAR_EMBEDDING_DIM", "768")))

    # --- timeouts in seconds (addendum 5.2) ---
    t_off: float = field(default_factory=lambda: _float("PEAR_T_OFF", 1.1))
    # Hard cap: past t_off, keep waiting (up to this) ONLY while Open Food Facts is still in flight
    # with no answer, so a cold TLS connection never reads as "barcode not found".
    t_off_max: float = field(default_factory=lambda: _float("PEAR_T_OFF_MAX", 2.5))
    t_fdc: float = field(default_factory=lambda: _float("PEAR_T_FDC", 1.5))
    t_label: float = field(default_factory=lambda: _float("PEAR_T_LABEL", 4.0))
    t_question: float = field(default_factory=lambda: _float("PEAR_T_QUESTION", 2.5))
    t_audio: float = field(default_factory=lambda: _float("PEAR_T_AUDIO", 4.0))
    t_text: float = field(default_factory=lambda: _float("PEAR_T_TEXT", 3.0))
    t_embed: float = field(default_factory=lambda: _float("PEAR_T_EMBED", 2.0))
    t_price: float = field(default_factory=lambda: _float("PEAR_T_PRICE", 6.0))
    t_picks_wait: float = field(default_factory=lambda: _float("PEAR_T_PICKS_WAIT", 2.5))
    t_firestore: float = field(default_factory=lambda: _float("PEAR_T_FIRESTORE", 1.0))

    # --- behaviour switches ---
    store: str = field(default_factory=lambda: _env("PEAR_STORE", "auto"))  # auto|firestore|memory
    auth_mode: str = field(default_factory=lambda: _env("PEAR_AUTH", "optional"))  # off|optional|required
    ask_engine: str = field(default_factory=lambda: _env("PEAR_ASK_ENGINE", "adk"))  # adk|direct
    trace: bool = field(default_factory=lambda: _bool("PEAR_TRACE", bool(_env("K_SERVICE"))))
    debug_routes: bool = field(default_factory=lambda: _bool("PEAR_DEBUG_ROUTES", True))
    log_format: str = field(
        default_factory=lambda: _env("PEAR_LOG_FORMAT", "json" if _env("K_SERVICE") else "pretty")
    )
    log_level: str = field(default_factory=lambda: _env("PEAR_LOG_LEVEL", "INFO"))

    # --- features (all on; flip any to 0 to cut it, addendum 16 cut order) ---
    feature_picks: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_PICKS", True))
    feature_vector: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_VECTOR", True))
    feature_prices: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_PRICES", True))
    feature_explain: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_EXPLAIN", True))
    feature_enrich: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_ENRICH", True))
    feature_scan_log: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_SCAN_LOG", True))
    feature_fdc: bool = field(default_factory=lambda: _bool("PEAR_FEATURE_FDC", True))

    # --- external APIs ---
    usda_api_key: str | None = field(default_factory=lambda: _env("USDA_API_KEY"))
    # Open Food Facts asks for "AppName/Version (contact)". Set PEAR_CONTACT_EMAIL on deploy.
    contact: str = field(default_factory=lambda: _env("PEAR_CONTACT_EMAIL", "pear-hackathon-app"))
    off_base: str = field(default_factory=lambda: _env("PEAR_OFF_BASE", "https://world.openfoodfacts.org"))
    obf_base: str = field(default_factory=lambda: _env("PEAR_OBF_BASE", "https://world.openbeautyfacts.org"))
    fdc_base: str = field(default_factory=lambda: _env("PEAR_FDC_BASE", "https://api.nal.usda.gov/fdc/v1"))

    fixtures_dir: Path = field(default_factory=_fixtures_dir)

    @property
    def user_agent(self) -> str:
        return f"Pear/{self.version} ({self.contact})"

    @property
    def features(self) -> dict[str, bool]:
        return {k.removeprefix("feature_"): v for k, v in asdict(self).items() if k.startswith("feature_")}

    def redacted(self) -> dict:
        d = {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(self).items()}
        if d.get("usda_api_key"):
            d["usda_api_key"] = "***set***"
        return d


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
