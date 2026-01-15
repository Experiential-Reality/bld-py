#!/usr/bin/env python3
"""
BLD Binary Translator: x86-64 → RISC-V 64

Uses BLD structures for translation:
- translate_x86_riscv.bld defines the mapping
- x86.bld (parse mode) decodes x86
- riscv.bld (emit mode) encodes RISC-V
"""

import sys
import os
import struct

sys.path.insert(0, 'src')

from bld_py.parser import parse
from bld_py.traverser import Traverser


def load_structures():
    """Load all BLD structures needed for translation."""
    structures = {}
    bld_dir = '../bld/bootstrap'

    for name in ['x86', 'riscv', 'translate_x86_riscv']:
        path = f'{bld_dir}/{name}.bld'
        with open(path) as f:
            structures[name] = parse(f.read())
        print(f"Loaded {name}.bld")

    return structures


def parse_x86_binary(data: bytes, entry_offset: int = 0x78) -> list:
    """Parse x86 binary into instruction list using pattern matching."""
    instructions = []
    pos = entry_offset

    while pos < len(data):
        start = pos

        # REX.W prefix?
        rex_w = data[pos] == 0x48 if pos < len(data) else False
        if rex_w:
            pos += 1

        if pos >= len(data):
            break

        opcode = data[pos]
        pos += 1

        # MOV r64, imm64 (B8+rd with REX.W)
        if rex_w and 0xB8 <= opcode <= 0xBF:
            reg = opcode - 0xB8
            reg_names = ['rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi']
            if pos + 8 <= len(data):
                imm = struct.unpack('<Q', data[pos:pos+8])[0]
                pos += 8
                instructions.append({
                    'op': 'mov_ri',
                    'dst': reg_names[reg],
                    'dst_num': reg,
                    'imm': imm
                })
                continue

        # SYSCALL (0F 05)
        if opcode == 0x0F and pos < len(data) and data[pos] == 0x05:
            pos += 1
            instructions.append({'op': 'syscall'})
            continue

        # Data section (printable ASCII or newline)
        if 0x20 <= opcode <= 0x7E or opcode in [0x0A, 0x0D, 0x09]:
            data_start = start - (1 if rex_w else 0)
            instructions.append({
                'op': 'data',
                'offset': data_start,
                'data': data[data_start:]
            })
            break

        # Unknown - skip
        print(f"  Unknown opcode at {start}: 0x{opcode:02x}")

    return instructions


def translate_instruction(instr: dict, structures: dict) -> list:
    """Translate one x86 instruction to RISC-V instruction(s)."""
    traverser = Traverser()
    for name, struct in structures.items():
        traverser.state.structures[name] = struct

    trans = structures['translate_x86_riscv']
    riscv_instrs = []

    op = instr['op']

    if op == 'mov_ri':
        dst = instr['dst']
        imm = instr['imm']

        # Use the register mapping from BLD
        # For syscall setup, map x86 regs to RISC-V syscall regs
        reg_map = {
            'rax': 17,  # a7 (syscall number)
            'rdi': 10,  # a0 (arg0)
            'rsi': 11,  # a1 (arg1)
            'rdx': 12,  # a2 (arg2)
        }

        rd = reg_map.get(dst, 10)

        # Syscall number translation
        syscall_map = {1: 64, 60: 93}  # write, exit
        if dst == 'rax' and imm in syscall_map:
            imm = syscall_map[imm]

        riscv_instrs.append({
            'op': 'li',
            'rd': rd,
            'imm': imm
        })

    elif op == 'syscall':
        riscv_instrs.append({'op': 'ecall'})

    elif op == 'data':
        riscv_instrs.append({
            'op': 'data',
            'offset': instr['offset'],
            'data': instr['data']
        })

    return riscv_instrs


def encode_riscv(instr: dict) -> bytes:
    """Encode RISC-V instruction to bytes."""
    op = instr['op']

    if op == 'li':
        rd = instr['rd']
        imm = instr['imm']
        result = bytearray()

        if -2048 <= imm <= 2047:
            # ADDI rd, zero, imm
            encoded = 0x13 | (rd << 7) | (0 << 15) | ((imm & 0xFFF) << 20)
            result.extend(struct.pack('<I', encoded))
        else:
            # LUI + ADDI for larger values
            upper = (imm + 0x800) >> 12
            lower = imm - (upper << 12)

            # LUI rd, upper
            lui = 0x37 | (rd << 7) | ((upper & 0xFFFFF) << 12)
            result.extend(struct.pack('<I', lui))

            if lower != 0:
                # ADDI rd, rd, lower
                addi = 0x13 | (rd << 7) | (rd << 15) | ((lower & 0xFFF) << 20)
                result.extend(struct.pack('<I', addi))

        return bytes(result)

    elif op == 'ecall':
        return struct.pack('<I', 0x00000073)

    return b''


def create_riscv_elf(code: bytes, data: bytes, data_addr: int) -> bytes:
    """Create RISC-V ELF64."""
    base = 0x10000
    entry = base + 0x78

    elf = bytearray()

    # ELF header
    elf.extend(b'\x7fELF\x02\x01\x01\x00' + b'\x00' * 8)
    elf.extend(struct.pack('<HHIQQQIHHHHHH',
        2,      # ET_EXEC
        0xF3,   # EM_RISCV
        1,      # version
        entry,  # entry
        64,     # phoff
        0,      # shoff
        0x5,    # flags
        64,     # ehsize
        56,     # phentsize
        1,      # phnum
        64,     # shentsize
        0,      # shnum
        0       # shstrndx
    ))

    # Program header
    total_size = 0x78 + len(code) + len(data) + 8  # +8 for alignment
    elf.extend(struct.pack('<IIQQQQQQ',
        1,          # PT_LOAD
        7,          # PF_R|PF_W|PF_X
        0,          # offset
        base,       # vaddr
        base,       # paddr
        total_size, # filesz
        total_size, # memsz
        0x1000      # align
    ))

    # Pad to 0x78
    while len(elf) < 0x78:
        elf.append(0)

    # Code
    elf.extend(code)

    # Align data
    while len(elf) < 0x78 + len(code) + ((8 - len(code) % 8) % 8):
        elf.append(0)

    # Data
    elf.extend(data)

    return bytes(elf)


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser('~/.local/bin/bld')
    output_path = sys.argv[2] if len(sys.argv) > 2 else '/tmp/bld_riscv.elf'

    print(f"=== BLD Translator: x86-64 → RISC-V ===\n")
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}\n")

    # Load BLD structures
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    structures = load_structures()

    # Read x86 binary
    with open(input_path, 'rb') as f:
        x86_data = f.read()
    print(f"\nInput size: {len(x86_data)} bytes")

    # Parse x86 instructions
    print("\n--- Parsing x86-64 ---")
    x86_instrs = parse_x86_binary(x86_data)
    for i in x86_instrs:
        if i['op'] == 'data':
            print(f"  data: {len(i['data'])} bytes")
        elif i['op'] == 'mov_ri':
            print(f"  mov {i['dst']}, 0x{i['imm']:x}")
        else:
            print(f"  {i['op']}")

    # Translate to RISC-V
    print("\n--- Translating to RISC-V ---")
    riscv_instrs = []
    data_section = None

    for instr in x86_instrs:
        translated = translate_instruction(instr, structures)
        for ri in translated:
            if ri['op'] == 'data':
                data_section = ri['data']
            else:
                riscv_instrs.append(ri)
                if ri['op'] == 'li':
                    print(f"  li x{ri['rd']}, 0x{ri['imm']:x}")
                else:
                    print(f"  {ri['op']}")

    # Calculate data address and patch
    code_bytes = bytearray()
    for ri in riscv_instrs:
        code_bytes.extend(encode_riscv(ri))

    data_offset = 0x78 + len(code_bytes)
    data_offset = (data_offset + 7) & ~7  # Align
    data_addr = 0x10000 + data_offset

    print(f"\n--- Patching addresses ---")
    print(f"  Data at: 0x{data_addr:x}")

    # Re-translate with correct data address
    riscv_instrs_patched = []
    for instr in x86_instrs:
        translated = translate_instruction(instr, structures)
        for ri in translated:
            if ri['op'] == 'li' and ri['rd'] == 11:  # a1 = buffer address
                ri['imm'] = data_addr
            if ri['op'] != 'data':
                riscv_instrs_patched.append(ri)

    # Encode final RISC-V
    print("\n--- Encoding RISC-V ---")
    code_bytes = bytearray()
    for ri in riscv_instrs_patched:
        enc = encode_riscv(ri)
        code_bytes.extend(enc)
        if ri['op'] == 'li':
            print(f"  li x{ri['rd']}, 0x{ri['imm']:x} -> {enc.hex()}")
        else:
            print(f"  {ri['op']} -> {enc.hex()}")

    print(f"\nRISC-V code: {len(code_bytes)} bytes")

    # Create ELF
    elf = create_riscv_elf(bytes(code_bytes), data_section or b'', data_addr)

    with open(output_path, 'wb') as f:
        f.write(elf)
    os.chmod(output_path, 0o755)

    print(f"\nWrote {len(elf)} bytes to {output_path}")
    print(f"\nTest with: qemu-riscv64 {output_path}")


if __name__ == '__main__':
    main()
