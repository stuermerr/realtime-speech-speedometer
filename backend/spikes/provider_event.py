from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """Minimally normalized event shared by the provider spike probes."""

    received_at_seconds: float
    type: str
    fields: Mapping[str, object]
