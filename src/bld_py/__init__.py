# bld-py: Python bootstrap interpreter for BLD
#
# This is the minimal interpreter that bootstraps BLD.
# It does ONLY:
#   1. Parse - .bld text → structure (B, L, D)
#   2. Traverse - visit B/L/D in order, execute semantics
#   3. Primitives - emit_byte, set, get, modrm, rex, etc.
#
# Everything else is written in BLD itself.

from .parser import parse, ParsedStructure, Boundary, Link, Dimension, Semantic
from .traverser import Traverser, State, calculate_cost, calculate_l_cost

__version__ = '0.1.0'
__all__ = [
    'parse',
    'ParsedStructure',
    'Boundary',
    'Link',
    'Dimension',
    'Semantic',
    'Traverser',
    'State',
    'calculate_cost',
    'calculate_l_cost',
]
