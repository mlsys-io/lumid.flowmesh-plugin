"""Host-neutral lum.id settings."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class CoreSettings:
    lum_id_base_url: str
    lumid_org_id: str


def load_core_settings() -> CoreSettings:
    return CoreSettings(
        lum_id_base_url=os.getenv("LUM_ID_BASE_URL", "https://lum.id").rstrip("/"),
        lumid_org_id=os.getenv("LUMID_ORG_ID", "lumid"),
    )
