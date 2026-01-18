# BLD Compose - The Conceptual Machine
#
# Three concepts: | (B), / (L), \n (D)


def compose(d):
    """The conceptual machine. d is raw D (path)."""
    out = bytearray()

    if '|' in d:  # B: boundary
        _, v = d.split('|', 1)
        out.extend(compose(v.strip()))
    elif d.startswith('0x'):  # hex
        out.append(int(d, 16) & 0xFF)
    elif d.isdigit():  # decimal
        out.append(int(d) & 0xFF)
    elif '/' in d:  # L: link
        for part in d.split('/'):
            out.extend(compose(part))
    elif '\n' in d:  # D: dimension
        for line in d.split('\n'):
            if line.strip():
                out.extend(compose(line.strip()))

    return bytes(out)


if __name__ == '__main__':
    import sys
    sys.stdout.buffer.write(compose(sys.argv[1]) if len(sys.argv) > 1 else b'')
