# BLD Traverser - Execute structures by traversal
#
# Cost(traverse) = B_visit + D_elements * L_step
#
# The traverser visits boundaries, links, dimensions in order,
# executing semantic annotations as primitive operations.
#
# Primitive semantics (what the interpreter understands):
#   - emit(value)           Write value to output
#   - emit_byte(byte)       Write single byte to output
#   - emit_bytes(...)       Write multiple bytes
#   - set(name, value)      Set state variable
#   - get(name)             Get state variable
#   - pattern(expr)         Pattern match (returns bool)
#   - text_match(text)      String comparison
#   - label(name)           Define label at current position
#   - jmp(label)            Jump to label (for linking)
#   - call(structure)       Call another structure (uses=...)
#
# Everything else (x86 encoding, ELF format, etc.) is defined
# IN BLD and composed from these primitives.
#
# Structure Composition (the key to self-hosting):
#   When a boundary partition has uses=X, call structure X
#   When a link has uses=X, call structure X with scoping
#   This enables compiler.bld to compose Analyzer, Optimizer, X86, ELF

from dataclasses import dataclass, field
from typing import Any, Optional, Callable
import math
from .parser import ParsedStructure, Boundary, Link, Dimension, Semantic


@dataclass
class State:
    """Traversal state with scope stack.

    BLD structure of State:
      B scope: global | local
        global -> structures, labels, relocations, output (shared)
        local -> variables (per-scope)

      D scopes: N [sequential]  # Stack of variable scopes

      L lookup: name -> value (deps=1)  # Search up scope chain
    """
    _scope_stack: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    output: bytearray = field(default_factory=bytearray)
    labels: dict[str, int] = field(default_factory=dict)
    relocations: list[tuple[int, str]] = field(default_factory=list)
    structures: dict[str, ParsedStructure] = field(default_factory=dict)

    @property
    def variables(self) -> dict[str, Any]:
        """Current scope's variables (for backward compatibility)."""
        return self._scope_stack[-1]

    def push_scope(self):
        """Enter new scope (for structure calls)."""
        self._scope_stack.append({})

    def pop_scope(self):
        """Exit scope, return to parent."""
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()

    def get(self, name: str, default: Any = None) -> Any:
        """Get variable, searching up scope chain."""
        for scope in reversed(self._scope_stack):
            if name in scope:
                return scope[name]
        return default

    def set(self, name: str, value: Any):
        """Set variable in current scope."""
        self._scope_stack[-1][name] = value

    def emit(self, value: Any):
        """Emit value to output."""
        if isinstance(value, int):
            # Single byte
            self.output.append(value & 0xFF)
        elif isinstance(value, bytes):
            self.output.extend(value)
        elif isinstance(value, bytearray):
            self.output.extend(value)
        elif isinstance(value, str):
            self.output.extend(value.encode('utf-8'))

    def emit_byte(self, byte: int):
        """Emit single byte."""
        self.output.append(byte & 0xFF)

    def emit_bytes(self, *args):
        """Emit multiple bytes."""
        for b in args:
            if isinstance(b, int):
                self.output.append(b & 0xFF)
            elif isinstance(b, (bytes, bytearray)):
                self.output.extend(b)

    def emit_u16(self, value: int):
        """Emit 16-bit little-endian."""
        self.output.extend(value.to_bytes(2, 'little'))

    def emit_u32(self, value: int):
        """Emit 32-bit little-endian."""
        self.output.extend(value.to_bytes(4, 'little'))

    def emit_u64(self, value: int):
        """Emit 64-bit little-endian."""
        self.output.extend(value.to_bytes(8, 'little'))

    def emit_i32(self, value: int):
        """Emit signed 32-bit little-endian."""
        self.output.extend(value.to_bytes(4, 'little', signed=True))

    def label(self, name: str):
        """Define label at current position."""
        self.labels[name] = len(self.output)

    def reloc(self, name: str):
        """Record relocation for later linking."""
        self.relocations.append((len(self.output), name))
        # Emit placeholder
        self.emit_u32(0)

    def resolve(self):
        """Resolve all relocations."""
        for offset, name in self.relocations:
            if name in self.labels:
                target = self.labels[name]
                # rel32 is relative to end of instruction
                rel = target - (offset + 4)
                self.output[offset:offset + 4] = rel.to_bytes(4, 'little', signed=True)


def calculate_l_cost(deps: int) -> float:
    """Calculate L cost from dependency count.

    The L Formula (exact theorem from KL divergence):
        L = -½ ln(1 - ρ²)

    Where ρ (correlation) is derived from deps:
        rho = 1 - 1/(deps + 1)

    Examples:
        deps=0 → rho=0 → L=0 (independent)
        deps=1 → rho=0.5 → L=0.144
        deps=2 → rho=0.667 → L=0.402
        deps=∞ → rho=1 → L=∞ (fully sequential)
    """
    if deps == 0:
        return 0.0
    rho = 1.0 - 1.0 / (deps + 1)
    if rho >= 1.0:
        return float('inf')
    return -0.5 * math.log(1 - rho ** 2)


def calculate_cost(structure: ParsedStructure) -> float:
    """Calculate total cost of a structure.

    Cost = B_cost + D × L_cost

    Where:
        B_cost = sum of partition counts (topological, invariant under D)
        D = product of dimension extents (repetition multiplier)
        L_cost = sum of link L costs (geometric, scales with D)
    """
    # B_cost: count boundary partitions
    b_cost = sum(len(b.partitions) for b in structure.boundaries)

    # D: product of dimension extents
    d_extent = 1
    for dim in structure.dimensions:
        try:
            extent = int(dim.extent)
            if extent > 1:
                d_extent *= extent
        except (ValueError, TypeError):
            pass  # Symbolic extent, skip

    # L_cost: sum of link costs
    l_cost = 0.0
    for link in structure.links:
        deps = int(link.attrs.get('deps', '0'))
        l_cost += calculate_l_cost(deps)

    return b_cost + d_extent * l_cost


class Traverser:
    """BLD structure traverser - executes structures by traversal."""

    def __init__(self):
        self.state = State()
        self.builtins = self._init_builtins()
        self.current_structure = None

    def _init_builtins(self) -> dict[str, Callable]:
        """Initialize builtin semantic handlers.

        BLD structure discovered via Q1:
          B semantic_category: emit | state | control | x86 | match | iteration | composition | parse

        27 handlers total, grouped by mutually exclusive category.
        """
        return {
            # === B emit: output primitives ===
            'emit': self._emit,
            'emit_byte': self._emit_byte,
            'emit_bytes': self._emit_bytes,
            'emit_u16': self._emit_u16,
            'emit_u32': self._emit_u32,
            'emit_u64': self._emit_u64,
            'emit_i32': self._emit_i32,

            # === B state: variable access ===
            'set': self._set,
            'get': self._get,

            # === B control: flow control ===
            'label': self._label,
            'reloc': self._reloc,
            'jmp': self._jmp,
            'jcc': self._jcc,

            # === B x86: encoding helpers (bootstrap only) ===
            'rex': self._rex,
            'modrm': self._modrm,
            'sib': self._sib,

            # === B match: pattern matching ===
            'pattern': self._pattern,
            'text_match': self._text_match,

            # === B iteration: loop control ===
            'order': self._order,
            'foreach': self._foreach,
            'deps': self._deps,

            # === B composition: structure calls ===
            'uses': self._uses,
            'select': self._select,

            # === B parse: parsing primitives (bootstrap only) ===
            'parse_bld': self._parse_bld,
            'peek_char': self._peek_char,
            'consume_char': self._consume_char,
            'match_string': self._match_string,
            'skip_whitespace': self._skip_whitespace,
            'read_identifier': self._read_identifier,
            'read_number': self._read_number,

            # === B binary: binary parsing (reverse of emit) ===
            'match_byte': self._match_byte,
            'read_byte': self._read_byte,
            'read_u16': self._read_u16,
            'read_u32': self._read_u32,
            'read_u64': self._read_u64,
            'decode_modrm': self._decode_modrm,
            'decode_rex': self._decode_rex,

            # === B codec: bidirectional primitives ===
            # Same structure works for emit OR parse based on mode_codec
            'codec_byte': self._codec_byte,
            'codec_u16': self._codec_u16,
            'codec_u32': self._codec_u32,
            'codec_u64': self._codec_u64,
            'codec_modrm': self._codec_modrm,
            'codec_rex': self._codec_rex,

            # === B metadata: documentation generation ===
            'desc': self._desc,
            'usage': self._usage,

            # === B introspection: structure reflection ===
            # Minimal set - most work happens via foreach + state access
            'foreach': self._foreach,
            'emit_field': self._emit_field,
            'emit_cost_formula': self._emit_cost_formula,
            'if_has': self._if_has,
        }

    def load_bootstrap(self):
        """Load all bootstrap structures for composition."""
        from .bootstrap import load_bootstrap as load_bootstrap_fn, get_bootstrap_dir
        structures = load_bootstrap_fn(get_bootstrap_dir())
        for name, struct in structures.items():
            self.state.structures[name] = struct

    def load_struct_to_state(self, struct: ParsedStructure):
        """Convert ParsedStructure to traversable state variables.

        This is the minimal bridge between Python and BLD. Instead of Python
        introspection primitives, we convert the structure to state that
        BLD structures can access via $variables and iterate via foreach.

        State variables set:
          $struct_name - structure name
          $struct_returns - returns value
          $struct_boundaries - list of boundary dicts
          $struct_links - list of link dicts
          $struct_dimensions - list of dimension dicts
        """
        self.state.set('struct_name', struct.name)
        self.state.set('struct_returns', struct.returns or '')
        self.state.set('struct_boundaries', [
            {
                'name': b.name,
                'partitions': [
                    {
                        'name': p.name,
                        'semantics': ', '.join(str(s) for s in p.semantics) if p.semantics else ''
                    }
                    for p in b.partitions
                ]
            }
            for b in struct.boundaries
        ])
        self.state.set('struct_links', [
            {
                'name': l.name,
                'source': l.source,
                'target': l.target,
                'deps': l.attrs.get('deps', '0'),
                'attrs': ', '.join(f"{k}={v}" for k, v in l.attrs.items() if k != 'deps')
            }
            for l in struct.links
        ])
        self.state.set('struct_dimensions', [
            {
                'name': d.name,
                'extent': str(d.extent),
                'props': ', '.join(d.props) if d.props else ''
            }
            for d in struct.dimensions
        ])
        # Also compute cost components for emitters
        b_cost = sum(len(b.partitions) for b in struct.boundaries)
        d_extent = 1
        for d in struct.dimensions:
            try:
                extent = int(d.extent)
                if extent > 1:
                    d_extent *= extent
            except:
                pass
        l_cost = sum(calculate_l_cost(int(l.attrs.get('deps', '0'))) for l in struct.links)
        total_cost = b_cost + d_extent * l_cost
        self.state.set('struct_b_cost', b_cost)
        self.state.set('struct_d_extent', d_extent)
        self.state.set('struct_l_cost', l_cost)
        self.state.set('struct_total_cost', total_cost)

    def traverse(self, structure: ParsedStructure, input_data: Any = None) -> Any:
        """Traverse a structure, executing it.

        BLD structure of traversal (discovered via three questions):
          D phases: 3 [sequential]
          L phase_dim: structure -> dimensions (deps=0)
          L phase_bnd: structure -> boundaries (deps=0)
          L phase_lnk: structure -> links (deps=1, sorted by deps)

        The traverser visits dimensions first (set up iteration bounds),
        then boundaries (dispatch structure), then links (in dependency order).
        """
        # Register structure for later calls
        self.state.structures[structure.name] = structure
        self.current_structure = structure

        # Set input if provided
        if input_data is not None:
            self.state.set('input', input_data)

        # Visit dimensions first (set up iteration bounds)
        for dim in structure.dimensions:
            self._visit_dimension(dim)

        # Visit boundaries (dispatch structure)
        for boundary in structure.boundaries:
            self._visit_boundary(boundary)

        # Visit links in dependency order
        sorted_links = self._sort_links(structure.links)
        for link in sorted_links:
            self._visit_link(link)

        # Resolve relocations
        self.state.resolve()

        return bytes(self.state.output) if self.state.output else self.state.get('result')

    def _visit_dimension(self, dim: Dimension):
        """Visit a dimension declaration."""
        extent = dim.extent
        # Try to evaluate extent
        if extent.isdigit():
            self.state.set(f'dim_{dim.name}', int(extent))
        elif extent == 'N':
            # Dynamic - will be set from input
            pass
        else:
            self.state.set(f'dim_{dim.name}', extent)

        # Store dimension metadata
        self.state.set(f'dim_{dim.name}_props', dim.props)

    def _iterate_dimension(self, dim_name: str, body_fn: Callable[[], None]):
        """Execute body for each index in dimension.

        BLD principle: D multiplies L - this is the multiplication.
        """
        extent = self.state.get(f'dim_{dim_name}', 1)
        if isinstance(extent, str):
            extent = 1  # Fallback for symbolic extents
        for i in range(extent):
            self.state.set(f'idx_{dim_name}', i)
            body_fn()

    def _visit_boundary(self, boundary: Boundary):
        """Visit a boundary declaration and dispatch to selected partition.

        BLD principle: B is topological - partitions are mutually exclusive.

        Mode selection:
          - If mode_{boundary.name} is set, dispatch to that partition only
          - If no mode set AND partitions have no uses=, execute all (simple emit case)
          - If no mode set AND partitions have uses=, skip (requires explicit selection)
        """
        mode = self.state.get(f'mode_{boundary.name}')

        if mode is None:
            # Check if any partition has uses= semantic (requires explicit selection)
            has_uses = any(
                any(s.name == 'uses' for s in p.semantics)
                for p in boundary.partitions
            )
            if has_uses:
                # Skip boundary - requires explicit mode selection
                return

            # No uses= - execute all partitions (backward compatible for simple emit)
            for partition in boundary.partitions:
                for semantic in partition.semantics:
                    self._execute_semantic(semantic)
        else:
            # Dispatch to matching partition only
            for partition in boundary.partitions:
                if partition.name == mode:
                    for semantic in partition.semantics:
                        self._execute_semantic(semantic)
                    break

    def _visit_link(self, link: Link):
        """Visit a link declaration.

        BLD principle: L is geometric - links connect structures across space.

        If uses= is specified, call that structure with proper scoping.
        If foreach was set, iterate over that dimension.
        """
        # Check for iteration context
        foreach_dim = self.state.get('_current_foreach')
        if foreach_dim:
            self.state.set('_current_foreach', None)  # Clear for next link
            # Execute link body in iteration context
            def link_body():
                self._execute_link_body(link)
            self._iterate_dimension(foreach_dim, link_body)
        else:
            self._execute_link_body(link)

    def _execute_link_body(self, link: Link):
        """Execute the actual link operation."""
        # Check for 'uses' attribute - means call another structure
        if 'uses' in link.attrs:
            struct_name = link.attrs['uses']
            if struct_name in self.state.structures:
                sub_struct = self.state.structures[struct_name]
                # Push scope for called structure
                self.state.push_scope()
                # Pass source as input if available
                source_val = self.state.get(link.source)
                if source_val is not None:
                    self.state.set('input', source_val)
                # Recursive traversal
                result = self.traverse(sub_struct)
                # Pop scope and bind result to target
                self.state.pop_scope()
                if link.target:
                    self.state.set(link.target, result)

    def _sort_links(self, links: list[Link]) -> list[Link]:
        """Sort links by dependency order (deps attribute)."""
        def get_deps(link):
            deps = link.attrs.get('deps', '0')
            try:
                return int(deps)
            except ValueError:
                return 0

        return sorted(links, key=get_deps)

    def _execute_semantic(self, semantic: Semantic):
        """Execute a semantic annotation."""
        handler = self.builtins.get(semantic.name)
        if handler:
            handler(*semantic.args)
        else:
            # Unknown semantic - could be a no-op or log warning
            pass

    # =========================================================================
    # Builtin semantic handlers
    #
    # B semantic_category: emit | state | control | x86 | match | iteration | composition | parse
    # =========================================================================

    # === B emit: output primitives ===

    def _emit(self, *args):
        for arg in args:
            self.state.emit(self._eval_arg(arg))

    def _emit_byte(self, arg):
        self.state.emit_byte(self._eval_arg(arg))

    def _emit_bytes(self, *args):
        self.state.emit_bytes(*[self._eval_arg(a) for a in args])

    def _emit_u16(self, arg):
        self.state.emit_u16(self._eval_arg(arg))

    def _emit_u32(self, arg):
        self.state.emit_u32(self._eval_arg(arg))

    def _emit_u64(self, arg):
        self.state.emit_u64(self._eval_arg(arg))

    def _emit_i32(self, arg):
        self.state.emit_i32(self._eval_arg(arg))

    # === B state: variable access ===

    def _set(self, name, value):
        self.state.set(name, self._eval_arg(value))

    def _get(self, name):
        return self.state.get(name)

    # === B metadata: documentation generation ===

    def _usage(self, text):
        """Store usage text for current command."""
        self.state.set('_current_usage', self._eval_arg(text))

    def _desc(self, text):
        """Emit formatted help line: usage padded to 30 chars + description."""
        usage = self.state.get('_current_usage') or ''
        desc = self._eval_arg(text)
        # Pad usage to 30 characters, then add description
        line = f"  {usage:<28} {desc}\n"
        self.state.emit(line)

    # === B introspection: minimal structure reflection ===
    # These 4 primitives replace 150+ lines of hardcoded introspection.
    # The input structure is converted to state via load_struct_to_state().

    def _foreach(self, array_name, *partition_names):
        """Iterate over state array, executing partitions for each item.

        Usage: foreach(struct_boundaries, emit_boundary_row)
        Sets $_current to each item, then visits named partitions.
        """
        array_name = self._eval_arg(array_name)
        array = self.state.get(array_name)
        if not array:
            return
        for item in array:
            self.state.set('_current', item)
            # Visit the named partition(s) in current structure
            for pname in partition_names:
                pname = self._eval_arg(pname)
                self._visit_partition_by_name(pname)

    def _visit_partition_by_name(self, partition_name):
        """Visit a partition by name in the current structure."""
        # Find partition in current structure and execute its semantics
        for boundary in self.current_structure.boundaries if self.current_structure else []:
            for partition in boundary.partitions:
                if partition.name == partition_name:
                    for semantic in partition.semantics:
                        self._execute_semantic(semantic)
                    return

    def _emit_field(self, field_path):
        """Emit a field from $_current or other state variable.

        Usage: emit_field("name") -> emits $_current["name"]
               emit_field("partitions.0.name") -> nested access
        """
        field_path = self._eval_arg(field_path)
        current = self.state.get('_current')
        if not current:
            return
        # Support simple field access
        if '.' not in field_path:
            value = current.get(field_path, '') if isinstance(current, dict) else ''
            self.state.emit(str(value))
        else:
            # Nested access: split by dots
            parts = field_path.split('.')
            value = current
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, '')
                elif isinstance(value, list) and part.isdigit():
                    idx = int(part)
                    value = value[idx] if idx < len(value) else ''
                else:
                    value = ''
                    break
            self.state.emit(str(value))

    def _emit_cost_formula(self):
        """Emit cost formula from pre-computed state variables."""
        b = self.state.get('struct_b_cost', 0)
        d = self.state.get('struct_d_extent', 1)
        l = self.state.get('struct_l_cost', 0)
        t = self.state.get('struct_total_cost', 0)
        self.state.emit(f"B + D×L = {b} + {d}×{l:.3f} = {t:.3f}")

    def _if_has(self, array_name, *partition_names):
        """Conditionally execute partitions if array is non-empty.

        Usage: if_has(struct_boundaries, emit_boundaries_section)
        """
        array_name = self._eval_arg(array_name)
        array = self.state.get(array_name)
        if array:
            for pname in partition_names:
                pname = self._eval_arg(pname)
                self._visit_partition_by_name(pname)

    # === B control: flow control ===

    def _label(self, name):
        self.state.label(name)

    def _reloc(self, name):
        self.state.reloc(name)

    def _jmp(self, label):
        # Emit JMP rel32
        self.state.emit_byte(0xE9)
        self.state.reloc(label)

    def _jcc(self, condition, label):
        # Emit Jcc rel32
        conditions = {
            'e': 0x84, 'ne': 0x85, 'z': 0x84, 'nz': 0x85,
            'l': 0x8C, 'ge': 0x8D, 'le': 0x8E, 'g': 0x8F,
        }
        cc = conditions.get(condition, 0x84)
        self.state.emit_bytes(0x0F, cc)
        self.state.reloc(label)

    # === B x86: encoding helpers (bootstrap only) ===

    def _rex(self, w=0, r=0, x=0, b=0):
        """Emit REX prefix if needed."""
        w = self._eval_arg(w)
        r = self._eval_arg(r)
        x = self._eval_arg(x)
        b = self._eval_arg(b)
        if w or r or x or b:
            rex = 0x40 | (w << 3) | (r << 2) | (x << 1) | b
            self.state.emit_byte(rex)

    def _modrm(self, mod, reg, rm):
        """Emit ModR/M byte."""
        mod = self._eval_arg(mod)
        reg = self._eval_arg(reg)
        rm = self._eval_arg(rm)
        modrm = ((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)
        self.state.emit_byte(modrm)

    def _sib(self, scale, index, base):
        """Emit SIB byte."""
        scale = self._eval_arg(scale)
        index = self._eval_arg(index)
        base = self._eval_arg(base)
        sib = ((scale & 3) << 6) | ((index & 7) << 3) | (base & 7)
        self.state.emit_byte(sib)

    # === B match: pattern matching ===

    def _pattern(self, *args):
        # Pattern matching - for now just return True
        return True

    def _text_match(self, text):
        # Text matching
        return self.state.get('current_text') == text

    # === B iteration: loop control ===

    def _order(self, n):
        self.state.set('_order', self._eval_arg(n))

    def _foreach_dim(self, dim_name):
        """Mark that current link should iterate over dimension.

        The actual iteration happens in _visit_link when this is detected.
        For now, store the dimension name to iterate over.
        """
        dim_name = self._eval_arg(dim_name)
        self.state.set('_current_foreach', dim_name)

    def _deps(self, n):
        self.state.set('_deps', self._eval_arg(n))

    # === B composition: structure calls ===

    def _uses(self, name):
        """Call another structure by name.

        This is how structures compose - when a partition has uses=X,
        it calls structure X with the current scope.
        """
        name = self._eval_arg(name)
        if name in self.state.structures:
            struct = self.state.structures[name]
            # Push scope for called structure
            self.state.push_scope()
            # Traverse the referenced structure
            result = self.traverse(struct)
            # Pop scope
            self.state.pop_scope()
            self.state.set('_uses_result', result)
        else:
            # Structure not loaded yet, just store the name
            self.state.set('_uses', name)

    def _select(self, boundary_name, partition_name):
        """Select which partition of a boundary to dispatch to.

        Usage: select(instruction, mov_rr) -> sets mode_instruction = 'mov_rr'
        """
        boundary_name = self._eval_arg(boundary_name)
        partition_name = self._eval_arg(partition_name)
        self.state.set(f'mode_{boundary_name}', partition_name)

    # === B parse: parsing primitives (bootstrap only) ===

    def _parse_bld(self, source_var, result_var='parsed'):
        """Parse BLD source text into a structure.

        This is the key primitive for self-hosting - enables BLD to compile BLD.
        """
        from .parser import parse
        source = self._eval_arg(source_var)
        if source.startswith('$'):
            source = self.state.get(source[1:])
        structure = parse(source)
        self.state.set(result_var, structure)
        return structure

    def _peek_char(self):
        """Peek at next character in parse input."""
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        if pos < len(source):
            return source[pos]
        return None

    def _consume_char(self):
        """Consume and return next character."""
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        if pos < len(source):
            char = source[pos]
            self.state.set('_parse_pos', pos + 1)
            return char
        return None

    def _match_string(self, expected):
        """Check if input matches expected string."""
        expected = self._eval_arg(expected)
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        if source[pos:pos + len(expected)] == expected:
            self.state.set('_parse_pos', pos + len(expected))
            return True
        return False

    def _skip_whitespace(self):
        """Skip whitespace in parse input."""
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        while pos < len(source) and source[pos] in ' \t\n\r':
            pos += 1
        self.state.set('_parse_pos', pos)

    def _read_identifier(self):
        """Read an identifier from input."""
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        start = pos
        if pos < len(source) and (source[pos].isalpha() or source[pos] == '_'):
            pos += 1
            while pos < len(source) and (source[pos].isalnum() or source[pos] == '_'):
                pos += 1
            self.state.set('_parse_pos', pos)
            return source[start:pos]
        return None

    def _read_number(self):
        """Read a number from input."""
        source = self.state.get('_parse_source', '')
        pos = self.state.get('_parse_pos', 0)
        start = pos
        if pos < len(source) and (source[pos].isdigit() or source[pos] == '-'):
            if source[pos] == '-':
                pos += 1
            # Handle hex
            if pos + 1 < len(source) and source[pos:pos + 2] == '0x':
                pos += 2
                while pos < len(source) and source[pos] in '0123456789abcdefABCDEF':
                    pos += 1
            else:
                while pos < len(source) and source[pos].isdigit():
                    pos += 1
            self.state.set('_parse_pos', pos)
            num_str = source[start:pos]
            if num_str.startswith('0x'):
                return int(num_str, 16)
            return int(num_str) if num_str else None
        return None

    def _eval_arg(self, arg) -> Any:
        """Evaluate a semantic argument.

        BLD structure discovered via Q1:
          B eval_type: expression | hex | bin | int | byte | string | variable | raw
            expression -> recursive eval with + or -
            hex -> int(arg, 16)
            bin -> int(arg, 2)
            int -> int(arg)
            byte -> ord(inner)
            string -> arg[1:-1]
            variable -> state.get(arg[1:])
            raw -> arg (identifier)

        Each arg matches exactly one partition (mutually exclusive).
        Expression evaluation is a primitive capability needed for x86 encoding.
        """
        if isinstance(arg, int):
            return arg
        if not isinstance(arg, str):
            return arg

        arg = arg.strip()

        # String literal - check FIRST to avoid parsing ' - ' in text as expression
        if arg.startswith('"') and arg.endswith('"'):
            s = arg[1:-1]
            # Process escape sequences
            s = s.replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
            s = s.replace('\\\\', '\\').replace('\\"', '"')
            return s

        # Byte literal like b'x' or b'\n'
        if arg.startswith("b'") and arg.endswith("'"):
            inner = arg[2:-1]
            if inner.startswith('\\'):
                escapes = {'n': ord('\n'), 't': ord('\t'), 'r': ord('\r'), '0': 0}
                return escapes.get(inner[1], ord(inner[1]))
            return ord(inner)

        # Expression with + (e.g., 0xB8 + $dst_lo)
        if ' + ' in arg:
            parts = arg.split(' + ')
            return sum(self._eval_arg(p.strip()) for p in parts)

        # Expression with - (e.g., $offset - 4)
        if ' - ' in arg and not arg.startswith('-'):
            parts = arg.split(' - ')
            result = self._eval_arg(parts[0].strip())
            for p in parts[1:]:
                result -= self._eval_arg(p.strip())
            return result

        # Hex literal
        if arg.startswith('0x'):
            return int(arg, 16)

        # Binary literal
        if arg.startswith('0b'):
            return int(arg, 2)

        # Decimal literal
        if arg.isdigit() or (arg.startswith('-') and arg[1:].isdigit()):
            return int(arg)

        # Variable reference
        if arg.startswith('$'):
            return self.state.get(arg[1:])

        # Raw string/identifier
        return arg

    # =========================================================================
    # Binary parsing primitives (reverse of emit)
    #
    # These enable bidirectional traversal - same structure can emit or parse.
    # B direction: emit | parse (symmetric operations)
    # =========================================================================

    def _match_byte(self, expected):
        """Match expected byte at current position (reverse of emit_byte).

        Returns True if match, advances position. False otherwise.
        """
        expected = self._eval_arg(expected)
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos < len(input_bytes) and input_bytes[pos] == expected:
            self.state.set('_binary_pos', pos + 1)
            return True
        return False

    def _read_byte(self):
        """Read and return next byte (reverse of emit_byte with variable)."""
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos < len(input_bytes):
            byte = input_bytes[pos]
            self.state.set('_binary_pos', pos + 1)
            return byte
        return None

    def _read_u16(self):
        """Read 16-bit little-endian value (reverse of emit_u16)."""
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos + 2 <= len(input_bytes):
            value = int.from_bytes(input_bytes[pos:pos+2], 'little')
            self.state.set('_binary_pos', pos + 2)
            return value
        return None

    def _read_u32(self):
        """Read 32-bit little-endian value (reverse of emit_u32)."""
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos + 4 <= len(input_bytes):
            value = int.from_bytes(input_bytes[pos:pos+4], 'little')
            self.state.set('_binary_pos', pos + 4)
            return value
        return None

    def _read_u64(self):
        """Read 64-bit little-endian value (reverse of emit_u64)."""
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos + 8 <= len(input_bytes):
            value = int.from_bytes(input_bytes[pos:pos+8], 'little')
            self.state.set('_binary_pos', pos + 8)
            return value
        return None

    def _decode_modrm(self):
        """Decode ModR/M byte (reverse of modrm).

        Sets mod, reg, rm variables from the byte.
        """
        byte = self._read_byte()
        if byte is not None:
            mod = (byte >> 6) & 0x3
            reg = (byte >> 3) & 0x7
            rm = byte & 0x7
            self.state.set('mod', mod)
            self.state.set('reg', reg)
            self.state.set('rm', rm)
            return True
        return False

    def _decode_rex(self):
        """Decode REX prefix (reverse of rex).

        Peeks at next byte, if REX prefix (0x40-0x4F), decodes and consumes.
        Sets rex_w, rex_r, rex_x, rex_b variables.
        """
        input_bytes = self.state.get('_binary_input', b'')
        pos = self.state.get('_binary_pos', 0)
        if pos < len(input_bytes):
            byte = input_bytes[pos]
            if 0x40 <= byte <= 0x4F:
                self.state.set('_binary_pos', pos + 1)
                self.state.set('rex_w', (byte >> 3) & 1)
                self.state.set('rex_r', (byte >> 2) & 1)
                self.state.set('rex_x', (byte >> 1) & 1)
                self.state.set('rex_b', byte & 1)
                return True
        # No REX prefix, set defaults
        self.state.set('rex_w', 0)
        self.state.set('rex_r', 0)
        self.state.set('rex_x', 0)
        self.state.set('rex_b', 0)
        return False

    # =========================================================================
    # Bidirectional codec primitives
    #
    # Same structure can emit OR parse based on mode_codec setting.
    # B mode_codec: emit | parse
    #   emit -> write bytes to output
    #   parse -> read bytes from input, match pattern
    #
    # This enables: x86.bld works as encoder AND decoder!
    # =========================================================================

    def _codec_byte(self, value):
        """Bidirectional byte codec.

        emit mode: emit_byte(value)
        parse mode: match_byte(value)
        """
        mode = self.state.get('mode_codec', 'emit')
        value = self._eval_arg(value)
        if mode == 'emit':
            self.state.emit_byte(value)
            return True
        else:  # parse
            return self._match_byte(value)

    def _codec_u16(self, value):
        """Bidirectional u16 codec."""
        mode = self.state.get('mode_codec', 'emit')
        value = self._eval_arg(value)
        if mode == 'emit':
            self.state.emit_u16(value)
            return True
        else:
            read_val = self._read_u16()
            return read_val == value

    def _codec_u32(self, value):
        """Bidirectional u32 codec."""
        mode = self.state.get('mode_codec', 'emit')
        value = self._eval_arg(value)
        if mode == 'emit':
            self.state.emit_u32(value)
            return True
        else:
            read_val = self._read_u32()
            return read_val == value

    def _codec_u64(self, value):
        """Bidirectional u64 codec."""
        mode = self.state.get('mode_codec', 'emit')
        value = self._eval_arg(value)
        if mode == 'emit':
            self.state.emit_u64(value)
            return True
        else:
            read_val = self._read_u64()
            if read_val is not None:
                self.state.set('_read_value', read_val)
                return True
            return False

    def _codec_modrm(self, mod, reg, rm):
        """Bidirectional ModR/M codec.

        emit mode: modrm(mod, reg, rm) -> emit byte
        parse mode: decode byte -> set mod, reg, rm
        """
        mode = self.state.get('mode_codec', 'emit')
        if mode == 'emit':
            mod = self._eval_arg(mod)
            reg = self._eval_arg(reg)
            rm = self._eval_arg(rm)
            modrm = ((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)
            self.state.emit_byte(modrm)
            return True
        else:  # parse
            return self._decode_modrm()

    def _codec_rex(self, w=0, r=0, x=0, b=0):
        """Bidirectional REX prefix codec.

        emit mode: rex(w, r, x, b) -> emit REX if needed
        parse mode: decode REX if present -> set rex_w/r/x/b
        """
        mode = self.state.get('mode_codec', 'emit')
        if mode == 'emit':
            w = self._eval_arg(w)
            r = self._eval_arg(r)
            x = self._eval_arg(x)
            b = self._eval_arg(b)
            if w or r or x or b:
                rex = 0x40 | (w << 3) | (r << 2) | (x << 1) | b
                self.state.emit_byte(rex)
            return True
        else:  # parse
            return self._decode_rex()
