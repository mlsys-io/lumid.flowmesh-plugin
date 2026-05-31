"""Host-neutral lum.id settings."""

import os
from dataclasses import dataclass
from typing import Any, Self


@dataclass(frozen=True)
class CoreSettings:
    lum_id_base_url: str
    lumid_org_id: str

    @classmethod
    def _env_fields(cls) -> dict[str, Any]:
        return {
            "lum_id_base_url": os.getenv("LUM_ID_BASE_URL", "https://lum.id").rstrip("/"),
            "lumid_org_id": os.getenv("LUMID_ORG_ID", "lumid"),
        }

    @classmethod
    def from_env(cls) -> Self:
        return cls(**cls._env_fields())
