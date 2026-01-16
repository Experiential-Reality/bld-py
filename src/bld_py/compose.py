# BLD Compose - The Minimal Kernel
#
# Cost = B + D × L
#
# B = partitions (constants, fields)
# L = links (path references)
# D = dimensions (repetition metadata)

import dataclasses
import math
import pathlib
import typing

BLD_SRC: typing.Final[pathlib.Path] = (
    pathlib.Path(__file__).parent.parent.parent.parent / 'bld' / 'src'
)


# =============================================================================
# STRUCTURE - What we extract from crystallized BLD
# =============================================================================

@dataclasses.dataclass(frozen=True, slots=True)
class Constant:
    """B partition: hex or decimal constant."""
    value: int
    line: int


@dataclasses.dataclass(frozen=True, slots=True)
class Field:
    """B partition: name: value."""
    name: str
    value: str
    line: int


@dataclasses.dataclass(frozen=True, slots=True)
class Link:
    """L: path reference to another file."""
    path: str
    line: int


@dataclasses.dataclass(frozen=True, slots=True)
class Dimension:
    """D: repetition metadata (N*unit)."""
    extent: int
    unit: str
    line: int


@dataclasses.dataclass(slots=True)
class Structure:
    """Parsed crystallized BLD structure."""
    path: str
    constants: list[Constant] = dataclasses.field(default_factory=list)
    fields: list[Field] = dataclasses.field(default_factory=list)
    links: list[Link] = dataclasses.field(default_factory=list)
    dimensions: list[Dimension] = dataclasses.field(default_factory=list)

    @property
    def b_cost(self) -> int:
        """B = number of partitions."""
        return len(self.constants) + len(self.fields)

    @property
    def d_cost(self) -> int:
        """D = product of dimension extents."""
        if not self.dimensions:
            return 1
        result = 1
        for d in self.dimensions:
            result *= d.extent
        return result

    @property
    def l_cost(self) -> float:
        """L = sum of link costs. L(n) = -½ln(1-ρ²) where ρ = 1-1/(n+1)."""
        if not self.links:
            return 0.0
        n = len(self.links)
        rho = 1.0 - 1.0 / (n + 1)
        return -0.5 * math.log(1 - rho ** 2)

    @property
    def cost(self) -> float:
        """Cost = B + D × L."""
        return self.b_cost + self.d_cost * self.l_cost


# =============================================================================
# LINE CLASSIFICATION - B partitions
# =============================================================================

@dataclasses.dataclass(frozen=True, slots=True)
class LineEmpty:
    """B empty: comment or blank."""
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class LineHex:
    """B hex: 0xNN."""
    value: int


@dataclasses.dataclass(frozen=True, slots=True)
class LineDecimal:
    """B decimal: NN."""
    value: int


@dataclasses.dataclass(frozen=True, slots=True)
class LineField:
    """B field: name: value."""
    name: str
    value: str


@dataclasses.dataclass(frozen=True, slots=True)
class LineDimension:
    """B dimension: N*unit."""
    extent: int
    unit: str


@dataclasses.dataclass(frozen=True, slots=True)
class LinePath:
    """B path: path/to/file."""
    path: str


Line: typing.TypeAlias = LineEmpty | LineHex | LineDecimal | LineField | LineDimension | LinePath


def classify(raw: str) -> Line:
    """B line: empty | hex | decimal | field | dimension | path."""
    line = raw.strip()

    # B empty
    if not line or line.startswith('#'):
        return LineEmpty()

    # B hex
    if line.startswith('0x'):
        return LineHex(int(line, 16))

    # B field
    if ':' in line:
        name, value = line.split(':', 1)
        return LineField(name.strip(), value.strip())

    # B decimal
    if line.isdigit():
        return LineDecimal(int(line))

    # B dimension
    if '*' in line:
        parts = line.split('*')
        try:
            extent = int(parts[0])
            unit = parts[1] if len(parts) > 1 else 'b'
            return LineDimension(extent, unit)
        except ValueError:
            return LineEmpty()

    # B path
    if '/' in line or line.replace('_', '').isalnum():
        return LinePath(line)

    return LineEmpty()


# =============================================================================
# PARSE - Extract structure from crystallized BLD
# =============================================================================

def parse(path: str, base: pathlib.Path | None = None) -> Structure | None:
    """Parse crystallized BLD to structure.

    L parse: path -> Structure
    """
    if base is None:
        base = BLD_SRC

    file = base / f"{path}.bld"
    if not file.exists():
        file = base / path
        if not file.exists():
            return None

    struct = Structure(path=path)

    for lineno, raw in enumerate(file.read_text().split('\n'), 1):
        match classify(raw):
            case LineEmpty():
                pass
            case LineHex(value=v):
                struct.constants.append(Constant(v, lineno))
            case LineDecimal(value=v):
                struct.constants.append(Constant(v, lineno))
            case LineField(name=n, value=v):
                struct.fields.append(Field(n, v, lineno))
            case LineDimension(extent=e, unit=u):
                struct.dimensions.append(Dimension(e, u, lineno))
            case LinePath(path=p):
                struct.links.append(Link(p, lineno))

    return struct


# =============================================================================
# COMPOSE - The one operation
# =============================================================================

def compose(path: str, base: pathlib.Path | None = None) -> bytes:
    """THE ONE OPERATION. L compose: path -> bytes."""
    if base is None:
        base = BLD_SRC

    file = base / f"{path}.bld"
    if not file.exists():
        file = base / path
        if not file.exists():
            return b''

    out = bytearray()

    for raw in file.read_text().split('\n'):
        match classify(raw):
            case LineEmpty():
                pass
            case LineHex(value=v):
                out.append(v & 0xFF)
            case LineDecimal(value=v):
                out.append(v & 0xFF)
            case LineField(value=v):
                # Recurse on field value
                if v.startswith('0x'):
                    out.append(int(v, 16) & 0xFF)
                elif v.isdigit():
                    out.append(int(v) & 0xFF)
                elif v:
                    out.extend(compose(v, base))
            case LineDimension():
                pass  # Metadata only
            case LinePath(path=p):
                out.extend(compose(p, base))

    return bytes(out)


# =============================================================================
# ANALYZE - Show structure and cost
# =============================================================================

def analyze(path: str, base: pathlib.Path | None = None, recursive: bool = False) -> str:
    """Analyze crystallized BLD structure.

    L analyze: path -> report
    """
    struct = parse(path, base)
    if not struct:
        return f"error: cannot parse {path}"

    lines = [
        f"# {path}",
        f"",
        f"B partitions: {struct.b_cost}",
        f"  constants: {len(struct.constants)}",
        f"  fields: {len(struct.fields)}",
        f"",
        f"L links: {len(struct.links)}",
    ]

    for link in struct.links:
        lines.append(f"  {link.path}")

    lines.extend([
        f"",
        f"D dimensions: {len(struct.dimensions)}",
    ])

    for dim in struct.dimensions:
        lines.append(f"  {dim.extent}*{dim.unit}")

    lines.extend([
        f"",
        f"Cost = B + D × L",
        f"     = {struct.b_cost} + {struct.d_cost} × {struct.l_cost:.3f}",
        f"     = {struct.cost:.3f}",
    ])

    if recursive and struct.links:
        lines.append(f"")
        lines.append(f"# Recursive analysis")
        total_cost = struct.cost
        for link in struct.links:
            sub = parse(link.path, base)
            if sub:
                total_cost += sub.cost
                lines.append(f"  {link.path}: cost={sub.cost:.3f}")
        lines.append(f"")
        lines.append(f"Total cost: {total_cost:.3f}")

    return '\n'.join(lines)


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print('usage: python -m bld_py.compose <command> <path> [output]')
        print('commands: compose, parse, analyze')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'compose' and len(sys.argv) >= 3:
        result = compose(sys.argv[2])
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
            print(f"error: cannot parse {sys.argv[2]}")

    elif cmd in ('help', '-h', '--help'):
        print('usage: python -m bld_py.compose <command> <path> [output]')
        print('commands:')
        print('  compose <path> [output]  - compose to bytes')
        print('  analyze <path> [-r]      - show structure and cost')
        print('  parse <path>             - parse to structure')

    else:
        # Default: compose
        result = compose(cmd)
        if len(sys.argv) >= 3:
            pathlib.Path(sys.argv[2]).write_bytes(result)
            print(f'{len(result)} bytes -> {sys.argv[2]}')
        else:
            sys.stdout.buffer.write(result)
