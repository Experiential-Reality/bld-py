#!/usr/bin/env python3
"""BLD Bootstrap - Minimal CLI.

Cost = B + D × L

usage: bld <path> [output]    - compose .bld file to bytes
       bld -x <expr>          - compose expression directly

examples:
  bld src/simple.bld out.bin  - compose simple.bld to out.bin
  bld src/simple.bld | xxd    - compose and view hex
  bld -x "4/pad"              - compose expression: 4 of pad
"""

import pathlib
import sys

from .compose import compose, resolve


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__.strip())
        return

    # Expression mode: bld -x "expression"
    if sys.argv[1] == '-x' and len(sys.argv) >= 3:
        result = compose(sys.argv[2])
        sys.stdout.buffer.write(result)
        return

    # File mode: bld <path> [output]
    path = pathlib.Path(sys.argv[1])
    if path.exists():
        content = path.read_text()
    else:
        content = resolve(sys.argv[1])
        if content is None:
            print(f'error: {sys.argv[1]} not found', file=sys.stderr)
            sys.exit(1)

    result = compose(content)

    if len(sys.argv) >= 3:
        out = pathlib.Path(sys.argv[2])
        out.write_bytes(result)
        print(f'{len(result)} bytes -> {out}')
    else:
        sys.stdout.buffer.write(result)


if __name__ == '__main__':
    main()
