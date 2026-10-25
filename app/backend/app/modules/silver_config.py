"""Which silver-zone experiment output the API serves: `promoted` (ours_opt, pool OPT) or `v1` (ours_full, pool C).

`KCF_SILVER_SOURCE=promoted|v1` (default promoted). The comparison bars (current, pop65_k, past_k, and
random_k when present) always come from pool C of the same file, so the headline and its baselines share
one hotspot set (NEW n_hot = 73 in the promoted run).
"""

from __future__ import annotations

import os
from typing import Final

SILVER_SOURCE_ENV: Final = "KCF_SILVER_SOURCE"
PROMOTED: Final = "promoted"
V1: Final = "v1"
_SOURCES: Final = {
    # source -> (sub-directory under experiments/silver/out, protocol file (relative to out), headline pool, our method)
    PROMOTED: ("opt/promoted", "opt/protocol_opt.json", "OPT", "ours_opt"),
    V1: ("", "protocol.json", "C", "ours_full"),
}
_raw = os.environ.get(SILVER_SOURCE_ENV, PROMOTED).strip().lower()
if _raw not in _SOURCES:
    raise ValueError(f"{SILVER_SOURCE_ENV} must be one of {sorted(_SOURCES)}, got {_raw!r}")
SILVER_SOURCE: Final = _raw
SILVER_RELPATH: Final[str] = _SOURCES[_raw][0]
SILVER_PROTOCOL: Final[str] = _SOURCES[_raw][1]
HEADLINE_VARIANT: Final = "harm"
HEADLINE_POOL: Final[str] = _SOURCES[_raw][2]
OURS: Final[str] = _SOURCES[_raw][3]
COMPARISON_POOL: Final = "C"
COMPARISON_METHODS: Final = ("current", "random_k", "pop65_k", "past_k")
# Presentation order: baselines first, then ours.
HEADLINE_METHODS: Final[tuple[str, ...]] = (*COMPARISON_METHODS, OURS)

__all__ = ["COMPARISON_METHODS", "COMPARISON_POOL", "HEADLINE_METHODS", "HEADLINE_POOL", "HEADLINE_VARIANT", "OURS",
           "PROMOTED", "SILVER_PROTOCOL", "SILVER_RELPATH", "SILVER_SOURCE", "V1"]
