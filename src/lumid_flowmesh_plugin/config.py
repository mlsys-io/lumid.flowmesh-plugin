"""FlowMesh-side configuration for the lum.id plugin."""

import os
from dataclasses import dataclass
from typing import Any

from ._core import CoreSettings


@dataclass(frozen=True)
class Settings(CoreSettings):
    runmesh_billing_base_url: str
    flowmesh_bridge_secret: str
    lumid_balance_guard_enabled: bool
    lumid_acl_db_path: str

    @classmethod
    def _env_fields(cls) -> dict[str, Any]:
        return super()._env_fields() | {
            "runmesh_billing_base_url": os.getenv("RUNMESH_BILLING_BASE_URL", "").rstrip("/"),
            "flowmesh_bridge_secret": os.getenv("FLOWMESH_BRIDGE_SECRET", ""),
            "lumid_balance_guard_enabled": os.getenv("LUMID_BALANCE_GUARD", "off").lower() == "on",
            "lumid_acl_db_path": os.getenv(
                "LUMID_ACL_DB_PATH", "/app/plugin-data/lumid_acl.sqlite"
            ),
        }
