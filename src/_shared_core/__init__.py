"""Host-neutral lum.id plugin building blocks.

Exposed inside each plugin via a `_core` symlink to this directory.
"""

from ._cache import TTLCache
from .config import CoreSettings, load_core_settings
from .identity import (
    IntrospectedToken,
    LumidIdentityProvider,
    build_email_cache,
)

__all__ = [
    "CoreSettings",
    "IntrospectedToken",
    "LumidIdentityProvider",
    "TTLCache",
    "build_email_cache",
    "load_core_settings",
]
