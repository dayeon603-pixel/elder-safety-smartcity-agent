"""Local metre offsets around a scene centre (deck.gl COORDINATE_SYSTEM.METER_OFFSETS) and back.

Equirectangular about the centre: x = R·cos(lat0)·Δlon, y = R·Δlat (radians). Over a 구 (< 10 km) the
error against deck.gl's own offset projection is below 0.1 %, far under footprint accuracy, and the
numbers fit a Float32 with centimetre precision, which lon/lat degrees in Float32 do not.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from .constants import EARTH_RADIUS_M

FloatArray = npt.NDArray[np.float64]


def to_local(lon: FloatArray, lat: FloatArray, lon0: float, lat0: float) -> tuple[FloatArray, FloatArray]:
    """(x, y) metres east/north of (lon0, lat0)."""
    k = math.pi / 180.0
    x: FloatArray = (lon - lon0) * k * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y: FloatArray = (lat - lat0) * k * EARTH_RADIUS_M
    return x, y


def to_lonlat(x: FloatArray, y: FloatArray, lon0: float, lat0: float) -> tuple[FloatArray, FloatArray]:
    """Inverse of `to_local`."""
    k = 180.0 / math.pi
    lon: FloatArray = lon0 + x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))) * k
    lat: FloatArray = lat0 + y / EARTH_RADIUS_M * k
    return lon, lat


def point_to_local(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    x, y = to_local(np.array([lon]), np.array([lat]), lon0, lat0)
    return float(x[0]), float(y[0])


__all__ = ["FloatArray", "point_to_local", "to_local", "to_lonlat"]
