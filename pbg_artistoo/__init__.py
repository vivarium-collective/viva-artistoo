"""pbg-artistoo: process-bigraph wrapper for the Artistoo Cellular Potts Model.

Wraps the real Artistoo JavaScript simulator (github:ingewortel/artistoo) via
a persistent Node.js bridge. Import :class:`ArtistooProcess` for the Process
class, or use the discoverable composite generators.
"""

from .processes import ArtistooProcess, CPMSortingProcess
from .composites import (
    artistoo_cell_migration,
    artistoo_cell_sorting,
    glazier_graner_checkerboard,
    glazier_graner_cell_sorting,
    glazier_graner_high_temperature,
)

__all__ = [
    "ArtistooProcess",
    "CPMSortingProcess",
    "artistoo_cell_migration",
    "artistoo_cell_sorting",
    "glazier_graner_checkerboard",
    "glazier_graner_cell_sorting",
    "glazier_graner_high_temperature",
]
