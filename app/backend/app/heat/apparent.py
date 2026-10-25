"""KMA 여름철 체감온도 from air temperature and relative humidity (the quantity 폭염특보 uses).

    Tw = Ta*atan[0.151977*(RH+8.313659)^0.5] + atan(Ta+RH) - atan(RH-1.676331)
         + 0.00391838*RH^1.5*atan(0.023101*RH) - 4.686035                 (Stull 2011 wet-bulb)
    AT = -0.2442 + 0.55399*Tw + 0.45535*Ta - 0.0022*Tw^2 + 0.00278*Tw*Ta + 3.0   (KMA 여름철 체감온도)

Sources: R. Stull (2011), "Wet-Bulb Temperature from Relative Humidity and Air Temperature",
J. Appl. Meteor. Climatol. 50(11), 2267-2269; 기상청 여름철 체감온도 산출식 (Ta in C, RH in %).
Mirrors experiments/heat/weather_hub.py (tests cross-check the two); kept separate so the API image
does not import the experiment tree.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def wet_bulb_stull(ta: FloatArray | float, rh: FloatArray | float) -> FloatArray:
    """Stull (2011) wet-bulb temperature (C) from air temperature (C) and relative humidity (%)."""
    t, h = np.asarray(ta, dtype=np.float64), np.asarray(rh, dtype=np.float64)
    out: FloatArray = (t * np.arctan(0.151977 * np.sqrt(h + 8.313659)) + np.arctan(t + h)
                       - np.arctan(h - 1.676331) + 0.00391838 * h**1.5 * np.arctan(0.023101 * h) - 4.686035)
    return out


def summer_apparent_temp(ta: FloatArray | float, rh: FloatArray | float) -> FloatArray:
    """KMA 여름철 체감온도 (C). NaN in -> NaN out."""
    t = np.asarray(ta, dtype=np.float64)
    tw = wet_bulb_stull(t, rh)
    out: FloatArray = -0.2442 + 0.55399 * tw + 0.45535 * t - 0.0022 * tw**2 + 0.00278 * tw * t + 3.0
    return out


__all__ = ["summer_apparent_temp", "wet_bulb_stull"]
