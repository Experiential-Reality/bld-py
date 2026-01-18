# BLD Compose - The Conceptual Machine
#
# Three concepts: | (B), / (L), \n (D)
# Bootstrap: resolves concepts to files

import pathlib

# Bootstrap: src directory for resolving concepts
SRC = pathlib.Path(__file__).parent.parent.parent.parent / 'bld' / 'src'


def resolve(path):
    """Resolve a concept path to file content. None if raw concept."""
    f = SRC / (path + '.bld')
    if f.exists():
        return f.read_text()
    return None


def compose(d):
    """The conceptual machine. d is raw D (path)."""
    out = bytearray()

    if '\n' in d:  # D: dimension (check first - file content has newlines)
        for line in d.split('\n'):
            if line.strip():
                out.extend(compose(line.strip()))
    elif '|' in d:  # B: boundary
        _, v = d.split('|', 1)
        out.extend(compose(v.strip()))
    elif d.startswith('0x'):  # hex
        out.append(int(d, 16) & 0xFF)
    elif d.isdigit():  # decimal
        out.append(int(d) & 0xFF)
    elif '/' in d:  # L: link - resolve or traverse
        content = resolve(d)
        if content:
            out.extend(compose(content))
        else:
            # Raw concept path - compose each segment
            for part in d.split('/'):
                out.extend(compose(part))

    return bytes(out)


if __name__ == '__main__':
    import sys
    sys.stdout.buffer.write(compose(sys.argv[1]) if len(sys.argv) > 1 else b'')
