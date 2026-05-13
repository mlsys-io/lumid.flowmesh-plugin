"""Environment-driven configuration for the lumid_flowmesh_plugin hooks."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    lum_id_base_url: str
    runmesh_billing_base_url: str
    flowmesh_bridge_secret: str
    lumid_balance_guard_enabled: bool
    lumid_org_id: str


def load_settings() -> Settings:
    return Settings(
        lum_id_base_url=os.getenv("LUM_ID_BASE_URL", "https://lum.id").rstrip("/"),
        runmesh_billing_base_url=os.getenv("RUNMESH_BILLING_BASE_URL", "").rstrip("/"),
        flowmesh_bridge_secret=os.getenv("FLOWMESH_BRIDGE_SECRET", ""),
        lumid_balance_guard_enabled=os.getenv("LUMID_BALANCE_GUARD", "off").lower() == "on",
        lumid_org_id=os.getenv("LUMID_ORG_ID", "lumid"),
    )
