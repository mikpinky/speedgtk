"""Animation controllers and geometry used by the speed gauge."""

from .needle import NeedleResetAnimation
from .ping import PingReadoutAnimation, PingToSpeedCrossfade
from .scale import GaugeScaleTransition

__all__ = (
    "GaugeScaleTransition",
    "NeedleResetAnimation",
    "PingReadoutAnimation",
    "PingToSpeedCrossfade",
)
