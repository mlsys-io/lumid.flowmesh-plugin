"""Host-neutral lum.id settings."""

import os
from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class CoreSettings:
    lum_id_base_url: str
    lumid_org_id: str

    @classmethod
    def _core_env_fields(cls) -> dict[str, str]:
        return {
            "lum_id_base_url": os.getenv("LUM_ID_BASE_URL", "https://lum.id").rstrip("/"),
            "lumid_org_id": os.getenv("LUMID_ORG_ID", "lumid"),
        }

    @classmethod
    def from_env(cls) -> Self:
        return cls(**cls._core_env_fields())
