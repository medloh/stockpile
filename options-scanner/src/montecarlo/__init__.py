"""Monte Carlo trade analyzer.

Vectorized NumPy GBM (optionally Merton-style earnings jumps), per-leg payoff
evaluation, and summary metrics for single- and multi-leg option positions.
Pure-Python; no Streamlit dependency.
"""

from .position import Leg, Position
from .engine import SimulationConfig, SimulationResult, run_simulation

__all__ = [
    "Leg",
    "Position",
    "SimulationConfig",
    "SimulationResult",
    "run_simulation",
]
