# bld-py: Minimal BLD Bootstrap
#
# Cost = B + D × L
#
# One operation: compose
#   crystallized BLD -> bytes
#
# Structure IS computation. The BLD files ARE the assembler.

from .compose import (
    compose,
    parse,
    analyze,
    classify,
    Structure,
    BLD_SRC,
)

__version__ = '0.5.0'
__all__ = [
    'compose',
    'parse',
    'analyze',
    'classify',
    'Structure',
    'BLD_SRC',
]
