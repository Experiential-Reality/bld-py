#!/usr/bin/env python3
"""BLD Bootstrap - Minimal Python bootstrap for BLD.

This is the ONLY Python needed. Everything else is BLD.

Usage: bld <command> [args]

Commands are defined in bootstrap/cli.bld and executed by traversing
the corresponding BLD structure.
"""

import sys
import os
from pathlib import Path

from .parser import parse
from .traverser import Traverser
from .bootstrap import load_bootstrap, get_bootstrap_dir


def main():
    """Minimal bootstrap: parse args, find command structure, traverse it."""
    t = Traverser()

    # Load all bootstrap structures
    for name, struct in load_bootstrap().items():
        t.state.structures[name] = struct

    # No args or help -> traverse CLI for help text
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help', 'help'):
        t.traverse(t.state.structures['CLI'])
        sys.stdout.buffer.write(bytes(t.state.output))
        return

    cmd = sys.argv[1]

    # Map commands to structures and set up state
    if cmd == 'run' and len(sys.argv) >= 3:
        # Run: parse file, traverse it
        with open(sys.argv[2]) as f:
            source = f.read()
        struct = parse(source)
        for name, s in load_bootstrap().items():
            t.state.structures[name] = s
        t.traverse(struct)
        if t.state.output:
            print(f"Output ({len(t.state.output)} bytes): {bytes(t.state.output).hex()}")

    elif cmd == 'parse' and len(sys.argv) >= 3:
        # Parse: load structure, traverse DisplayEmitter
        with open(sys.argv[2]) as f:
            source = f.read()
        struct = parse(source)
        t.load_struct_to_state(struct)
        t.state.set('source_file', sys.argv[2])
        if 'DisplayEmitter' in t.state.structures:
            t.traverse(t.state.structures['DisplayEmitter'])
            sys.stdout.buffer.write(bytes(t.state.output))
        else:
            # Fallback to simple display
            print(f"structure {struct.name}\n")
            for b in struct.boundaries:
                parts = ' | '.join(p.name for p in b.partitions)
                print(f"B {b.name}: {parts}")
                for p in b.partitions:
                    if p.semantics:
                        sems = ', '.join(str(s) for s in p.semantics)
                        print(f"  {p.name} -> {sems}")
            print()
            for l in struct.links:
                attrs = ', '.join(f"{k}={v}" for k, v in l.attrs.items())
                print(f"L {l.name}: {l.source} -> {l.target}" + (f" ({attrs})" if attrs else ""))
            print()
            for d in struct.dimensions:
                props = f" [{', '.join(d.props)}]" if d.props else ""
                print(f"D {d.name}: {d.extent}{props}")
            if struct.returns:
                print(f"\nreturns: {struct.returns}")

    elif cmd == 'cost' and len(sys.argv) >= 3:
        # Cost: load structure, traverse CostEmitter
        with open(sys.argv[2]) as f:
            source = f.read()
        struct = parse(source)
        t.load_struct_to_state(struct)
        if 'CostEmitter' in t.state.structures:
            t.traverse(t.state.structures['CostEmitter'])
            sys.stdout.buffer.write(bytes(t.state.output))
        else:
            # Fallback
            from .traverser import calculate_cost
            print(f"Structure: {struct.name}")
            print(f"Cost = {calculate_cost(struct):.3f}")

    elif cmd == 'markdown' and len(sys.argv) >= 3:
        # Markdown: load structure, traverse MarkdownEmitter
        with open(sys.argv[2]) as f:
            source = f.read()
        struct = parse(source)
        t.load_struct_to_state(struct)
        t.state.set('source_file', sys.argv[2])
        if 'MarkdownEmitter' in t.state.structures:
            t.traverse(t.state.structures['MarkdownEmitter'])
            output = bytes(t.state.output).decode()
        else:
            # Fallback - should not happen once MarkdownEmitter works
            output = f"# {struct.name}\n\nMarkdownEmitter not loaded.\n"

        if '-o' in sys.argv:
            idx = sys.argv.index('-o')
            if idx + 1 < len(sys.argv):
                with open(sys.argv[idx + 1], 'w') as f:
                    f.write(output)
                print(f"Generated: {sys.argv[idx + 1]}")
                return
        print(output)

    elif cmd == 'compile' and len(sys.argv) >= 3:
        # Compile: load structure, use Lowering + ELF64
        with open(sys.argv[2]) as f:
            source = f.read()
        struct = parse(source)
        print(f"Compiling: {struct.name}")

        # Get output path
        output_path = 'a.out'
        if '-o' in sys.argv:
            idx = sys.argv.index('-o')
            if idx + 1 < len(sys.argv):
                output_path = sys.argv[idx + 1]

        # Generate help or name
        if '--with-help' in sys.argv:
            t2 = Traverser()
            for name, s in load_bootstrap().items():
                t2.state.structures[name] = s
            t2.traverse(t2.state.structures['CLI'])
            message = bytes(t2.state.output)
        else:
            message = f"{struct.name}\n".encode()

        base_addr = 0x400000
        code_offset = 0x78

        # Generate write syscall
        t.state.set('mode_operation', 'syscall_write')
        t.state.set('buffer', base_addr + code_offset + 64)
        t.state.set('length', len(message))
        t.traverse(t.state.structures['Lowering'])
        write_code = bytes(t.state.output)

        # Generate exit syscall
        t3 = Traverser()
        for name, s in load_bootstrap().items():
            t3.state.structures[name] = s
        t3.state.set('mode_operation', 'syscall_exit')
        t3.state.set('exit_code', 0)
        t3.traverse(t3.state.structures['Lowering'])
        exit_code = bytes(t3.state.output)

        # Recalculate with correct address
        string_addr = base_addr + code_offset + len(write_code) + len(exit_code)
        t4 = Traverser()
        for name, s in load_bootstrap().items():
            t4.state.structures[name] = s
        t4.state.set('mode_operation', 'syscall_write')
        t4.state.set('buffer', string_addr)
        t4.state.set('length', len(message))
        t4.traverse(t4.state.structures['Lowering'])
        code = bytes(t4.state.output) + exit_code

        # Wrap in ELF
        t5 = Traverser()
        for name, s in load_bootstrap().items():
            t5.state.structures[name] = s
        t5.state.set('entry', base_addr + code_offset)
        t5.state.set('base', base_addr)
        t5.state.set('filesz', code_offset + len(code) + len(message))
        t5.state.set('code', code + message)
        t5.state.set('mode_emit', 'full')
        t5.traverse(t5.state.structures['ELF64'])

        binary = bytes(t5.state.output)
        with open(output_path, 'wb') as f:
            f.write(binary)
        os.chmod(output_path, 0o755)
        print(f"Output: {output_path} ({len(binary)} bytes)")

    elif cmd == 'generate':
        # Generate: traverse PythonEmitter for parser.bld and traverser.bld
        bootstrap_dir = get_bootstrap_dir()
        output_dir = bootstrap_dir.parent.parent / 'bld-py' / 'src' / 'bld_py'

        for source_name, output_name in [('parser.bld', 'parser.py'), ('traverser.bld', 'traverser.py')]:
            source_path = bootstrap_dir / source_name
            with open(source_path) as f:
                source = f.read()
            struct = parse(source)

            t = Traverser()
            for name, s in load_bootstrap().items():
                t.state.structures[name] = s
            t.load_struct_to_state(struct)
            t.state.set('source_file', source_name)

            if 'PythonEmitter' in t.state.structures:
                t.traverse(t.state.structures['PythonEmitter'])
                output = bytes(t.state.output).decode()
                output_path = output_dir / output_name
                with open(output_path, 'w') as f:
                    f.write(output)
                print(f"Generated: {output_path}")
            else:
                print(f"PythonEmitter not found - cannot generate {output_name}")

    else:
        # Unknown command - show help
        t.traverse(t.state.structures['CLI'])
        sys.stdout.buffer.write(bytes(t.state.output))
        sys.exit(1)


if __name__ == '__main__':
    main()
