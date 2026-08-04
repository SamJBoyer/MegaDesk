"""Public API for Executive canvas plugins.

External tools should import from this package after installing Executive
into the ``loot`` conda environment::

    from executive import BaseNode, register
"""

from engine.base_node import BaseNode
from engine.registry import register

__all__ = ["BaseNode", "register"]
