#!/usr/bin/env python3
"""BLD Bootstrap - Minimal CLI.

Cost = B + D × L

usage: python -m bld_py <command> <path> [output]

commands:
  compose <path> [output]  - compose to bytes
  analyze <path> [-r]      - show structure and cost
  parse <path>             - parse to structure
"""

import pathlib
import sys

from .compose import compose, parse, analyze


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__.strip())
        return

    cmd = sys.argv[1]

    if cmd == 'compose' and len(sys.argv) >= 3:
        result = compose(sys.argv[2])
        if not result:
            print(f'error: cannot compose {sys.argv[2]}', file=sys.stderr)
            sys.exit(1)
        if len(sys.argv) >= 4:
            pathlib.Path(sys.argv[3]).write_bytes(result)
            print(f'{len(result)} bytes -> {sys.argv[3]}')
        else:
            sys.stdout.buffer.write(result)

    elif cmd == 'analyze' and len(sys.argv) >= 3:
        recursive = '-r' in sys.argv
        print(analyze(sys.argv[2], recursive=recursive))

    elif cmd == 'parse' and len(sys.argv) >= 3:
        struct = parse(sys.argv[2])
        if struct:
            print(f"path: {struct.path}")
            print(f"constants: {len(struct.constants)}")
            print(f"fields: {len(struct.fields)}")
            print(f"links: {len(struct.links)}")
            print(f"dimensions: {len(struct.dimensions)}")
            print(f"cost: {struct.cost:.3f}")
        else:
            print(f"error: cannot parse {sys.argv[2]}", file=sys.stderr)
            sys.exit(1)

    else:
        # Default: treat first arg as path, compose it
        result = compose(cmd)
        if not result:
            print(f'error: cannot compose {cmd}', file=sys.stderr)
            sys.exit(1)
        if len(sys.argv) >= 3:
            pathlib.Path(sys.argv[2]).write_bytes(result)
            print(f'{len(result)} bytes -> {sys.argv[2]}')
        else:
            sys.stdout.buffer.write(result)


if __name__ == '__main__':
    main()
