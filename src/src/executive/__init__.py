"""Public API for MegaDesk canvas plugins.

External tools should import from this package after installing megadesk
into the MegaDesk conda environment::

    from executive import BaseNode, register
"""

from engine.base_node import BaseNode
from engine.registry import register

__all__ = ["BaseNode", "register"]
