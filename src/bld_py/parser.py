# BLD Parser - Parse .bld text into structure
#
# Cost(parse) = B_grammar + D_tokens * L_match
#
# This parser is minimal. It recognizes:
#   - structure <name>
#   - B <name>: partition | partition | ...
#       partition -> semantic(args), semantic(args)
#   - L <name>: source -> target (attr=val, attr=val)
#   - D <name>: extent [prop, prop]
#   - P <name>: type [prop, prop]
#   - returns: type
#   - # comments

from dataclasses import dataclass, field
from typing import Optional
import re


@dataclass
class Semantic:
    """A semantic annotation like pattern(b'x') or emit_byte(0x55)."""
    name: str
    args: list[str] = field(default_factory=list)

    def __repr__(self):
        if self.args:
            return f"{self.name}({', '.join(self.args)})"
        return self.name


@dataclass
class Partition:
    """A partition in a boundary, e.g., 'empty -> skip_loop'."""
    name: str
    semantics: list[Semantic] = field(default_factory=list)


@dataclass
class Boundary:
    """B declaration: behavior partitioning."""
    name: str
    partitions: list[Partition] = field(default_factory=list)


@dataclass
class Link:
    """L declaration: connection/data flow."""
    name: str
    source: str
    target: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class Dimension:
    """D declaration: repetition axis."""
    name: str
    extent: str
    props: list[str] = field(default_factory=list)


@dataclass
class Parameter:
    """P declaration: typed parameter."""
    name: str
    type: str
    props: list[str] = field(default_factory=list)


@dataclass
class ParsedStructure:
    """A complete parsed .bld structure."""
    name: str
    boundaries: list[Boundary] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    returns: Optional[str] = None

    def get_boundary(self, name: str) -> Optional[Boundary]:
        for b in self.boundaries:
            if b.name == name:
                return b
        return None

    def get_link(self, name: str) -> Optional[Link]:
        for l in self.links:
            if l.name == name:
                return l
        return None

    def get_dimension(self, name: str) -> Optional[Dimension]:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None


def _classify_line(stripped: str, line: str) -> tuple[str, str]:
    """B line_type: classify line into exactly one partition.

    Boundaries discovered via Q1:
      B line_type: structure | boundary | link | dimension | parameter | returns | semantic | skip
    """
    if not stripped or stripped.startswith('#'):
        return ('skip', '')
    if stripped.startswith('structure '):
        return ('structure', stripped[10:].strip())
    if stripped.startswith('B '):
        return ('boundary', stripped[2:])
    if stripped.startswith('L '):
        return ('link', stripped[2:])
    if stripped.startswith('D '):
        return ('dimension', stripped[2:])
    if stripped.startswith('P '):
        return ('parameter', stripped[2:])
    if stripped.startswith('returns:'):
        return ('returns', stripped[8:].strip())
    if line.startswith('  ') and '->' in stripped:
        return ('semantic', stripped)
    return ('skip', '')


def parse(source: str) -> ParsedStructure:
    """Parse BLD source text into a ParsedStructure.

    BLD structure of parser:
      D lines: N [sequential]
      L classify: line -> line_type (deps=0)
      L parse_element: (line, line_type) -> element (deps=1)
      L accumulate: element -> structure (deps=1)
    """
    lines = source.split('\n')
    structure = None
    current_boundary = None

    for line in lines:
        stripped = line.strip()

        # B line_type dispatch - mutually exclusive partitions
        match _classify_line(stripped, line):
            case ('structure', name):
                structure = ParsedStructure(name=name)
            case ('boundary', text) if structure:
                current_boundary = _parse_boundary(text)
                structure.boundaries.append(current_boundary)
            case ('link', text) if structure:
                structure.links.append(_parse_link(text))
                current_boundary = None
            case ('dimension', text) if structure:
                structure.dimensions.append(_parse_dimension(text))
                current_boundary = None
            case ('parameter', text) if structure:
                structure.parameters.append(_parse_parameter(text))
                current_boundary = None
            case ('returns', value) if structure:
                structure.returns = value
                current_boundary = None
            case ('semantic', text) if current_boundary:
                partition = _parse_semantic_line(text)
                # Find matching partition and add semantics
                for p in current_boundary.partitions:
                    if p.name == partition.name:
                        p.semantics.extend(partition.semantics)
                        break
            case ('skip', _):
                pass

    return structure


def _parse_boundary(text: str) -> Boundary:
    """Parse: name: part1 | part2 | part3"""
    if ':' not in text:
        return Boundary(name=text.strip())

    name, rest = text.split(':', 1)
    partitions = []
    for part in rest.split('|'):
        part = part.strip()
        if part:
            partitions.append(Partition(name=part))

    return Boundary(name=name.strip(), partitions=partitions)


def _parse_link(text: str) -> Link:
    """Parse: name: source -> target (attrs)"""
    # Extract attributes in parentheses
    attrs = {}
    if '(' in text:
        attr_start = text.index('(')
        attr_end = text.rindex(')')
        attr_text = text[attr_start + 1:attr_end]
        text = text[:attr_start] + text[attr_end + 1:]

        for attr in attr_text.split(','):
            if '=' in attr:
                k, v = attr.split('=', 1)
                attrs[k.strip()] = v.strip()

    if ':' not in text:
        return Link(name=text.strip(), source='', target='', attrs=attrs)

    name, rest = text.split(':', 1)

    if '->' in rest:
        source, target = rest.split('->', 1)
        return Link(
            name=name.strip(),
            source=source.strip(),
            target=target.strip(),
            attrs=attrs
        )

    return Link(name=name.strip(), source=rest.strip(), target='', attrs=attrs)


def _parse_dimension(text: str) -> Dimension:
    """Parse: name: extent [props]"""
    props = []
    if '[' in text:
        prop_start = text.index('[')
        prop_end = text.rindex(']')
        prop_text = text[prop_start + 1:prop_end]
        text = text[:prop_start]
        props = [p.strip() for p in prop_text.split(',') if p.strip()]

    if ':' not in text:
        return Dimension(name=text.strip(), extent='1', props=props)

    name, extent = text.split(':', 1)
    return Dimension(name=name.strip(), extent=extent.strip(), props=props)


def _parse_parameter(text: str) -> Parameter:
    """Parse: name: type [props]"""
    props = []
    if '[' in text:
        prop_start = text.index('[')
        prop_end = text.rindex(']')
        prop_text = text[prop_start + 1:prop_end]
        text = text[:prop_start]
        props = [p.strip() for p in prop_text.split(',') if p.strip()]

    if ':' not in text:
        return Parameter(name=text.strip(), type='any', props=props)

    name, typ = text.split(':', 1)
    return Parameter(name=name.strip(), type=typ.strip(), props=props)


def _parse_semantic_line(text: str) -> Partition:
    """Parse: partition_name -> semantic(args), semantic(args)"""
    if '->' not in text:
        return Partition(name=text.strip())

    name, rest = text.split('->', 1)
    semantics = _parse_semantics(rest)
    return Partition(name=name.strip(), semantics=semantics)


def _parse_semantics(text: str) -> list[Semantic]:
    """Parse comma-separated semantics like: pattern(b'x'), action(emit), uses=Parser"""
    semantics = []

    # Split by comma (respecting parens)
    parts = _split_semantic_parts(text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Handle key=value syntax (e.g., uses=Parser)
        if '=' in part and '(' not in part:
            key, value = part.split('=', 1)
            semantics.append(Semantic(name=key.strip(), args=[value.strip()]))
        # Handle name(args) syntax
        elif '(' in part:
            name, args_str = _parse_call(part)
            if name:
                args = _split_args(args_str) if args_str else []
                semantics.append(Semantic(name=name, args=args))
        # Handle bare name
        elif part:
            semantics.append(Semantic(name=part, args=[]))

    return semantics


def _parse_call(text: str) -> tuple[str, str]:
    """Parse name(args) respecting quoted strings with parens inside.

    Returns (name, args_str) or (None, None) if no match.
    """
    # Find the opening paren
    open_idx = text.find('(')
    if open_idx == -1:
        return None, None

    name = text[:open_idx].strip()
    if not name or not name.isidentifier():
        return None, None

    # Find the matching close paren, tracking quotes
    depth = 1
    in_quote = False
    quote_char = None
    i = open_idx + 1

    while i < len(text) and depth > 0:
        char = text[i]
        if char in '"\'`' and not in_quote:
            in_quote = True
            quote_char = char
        elif char == quote_char and in_quote:
            in_quote = False
            quote_char = None
        elif char == '(' and not in_quote:
            depth += 1
        elif char == ')' and not in_quote:
            depth -= 1
        i += 1

    if depth != 0:
        return None, None

    args_str = text[open_idx + 1:i - 1]
    return name, args_str


def _split_semantic_parts(text: str) -> list[str]:
    """Split text by commas, respecting parentheses."""
    parts = []
    current = []
    depth = 0

    for char in text:
        if char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(char)

    if current:
        parts.append(''.join(current))

    return parts


def _split_args(text: str) -> list[str]:
    """Split arguments respecting quotes and nested parens."""
    args = []
    current = []
    depth = 0
    in_quote = False
    quote_char = None

    for char in text:
        if char in '"\'`' and not in_quote:
            in_quote = True
            quote_char = char
            current.append(char)
        elif char == quote_char and in_quote:
            in_quote = False
            quote_char = None
            current.append(char)
        elif char == '(' and not in_quote:
            depth += 1
            current.append(char)
        elif char == ')' and not in_quote:
            depth -= 1
            current.append(char)
        elif char == ',' and depth == 0 and not in_quote:
            arg = ''.join(current).strip()
            if arg:
                args.append(arg)
            current = []
        else:
            current.append(char)

    arg = ''.join(current).strip()
    if arg:
        args.append(arg)

    return args
