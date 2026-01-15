#!/usr/bin/env python3
"""Comprehensive test of x86.bld instruction encodings."""

import sys
import os
import tempfile
import subprocess

sys.path.insert(0, 'src')

from bld_py.parser import parse
from bld_py.traverser import Traverser, State


def emit_instruction(traverser, instruction, **kwargs):
    """Emit a single instruction using the BLD traverser."""
    # Reset output for fresh instruction
    traverser.state.output = bytearray()

    # Set the instruction mode
    traverser.state.set('mode_instruction', instruction)

    # Set all parameters
    for key, value in kwargs.items():
        traverser.state.set(key, value)

    # Find and execute just the instruction boundary
    x86_struct = traverser.state.structures['X86']
    for boundary in x86_struct.boundaries:
        if boundary.name == 'instruction':
            for partition in boundary.partitions:
                if partition.name == instruction:
                    for semantic in partition.semantics:
                        traverser._execute_semantic(semantic)
                    break
            break

    return bytes(traverser.state.output)


def test_instruction_encodings():
    """Test various instruction encodings against expected bytes."""
    print("=== Test: Instruction Encodings ===\n")

    # Load x86.bld
    with open('../bld/bootstrap/x86.bld') as f:
        x86_source = f.read()
    x86_struct = parse(x86_source)

    traverser = Traverser()
    traverser.state.structures['X86'] = x86_struct

    tests = [
        # (instruction, kwargs, expected_hex, description)
        ('nop', {}, '90', 'NOP'),
        ('syscall', {}, '0f05', 'SYSCALL'),
        ('ret', {}, 'c3', 'RET'),
        ('leave', {}, 'c9', 'LEAVE'),
        ('int3', {}, 'cc', 'INT3'),
        ('ud2', {}, '0f0b', 'UD2'),

        # MOV r64, imm64
        ('mov_ri', {'dst_ext': 0, 'dst_lo': 0, 'imm': 0x42}, '48b84200000000000000', 'MOV RAX, 0x42'),
        ('mov_ri', {'dst_ext': 0, 'dst_lo': 7, 'imm': 1}, '48bf0100000000000000', 'MOV RDI, 1'),

        # PUSH/POP
        ('push', {'reg_ext': 0, 'reg_lo': 5}, '55', 'PUSH RBP'),
        ('pop', {'reg_ext': 0, 'reg_lo': 5}, '5d', 'POP RBP'),
        ('push', {'reg_ext': 1, 'reg_lo': 0}, '4150', 'PUSH R8'),  # REX.B needed

        # ADD r64, r64
        ('add_rr', {'src': 3, 'dst': 0}, '4801d8', 'ADD RAX, RBX'),

        # SUB r64, imm32
        ('sub_ri', {'dst': 4, 'imm': 8}, '4881ec08000000', 'SUB RSP, 8'),

        # XOR r64, r64 (common idiom to zero register)
        ('xor_rr', {'src': 0, 'dst': 0}, '4831c0', 'XOR RAX, RAX'),

        # CMP r64, r64
        ('cmp_rr', {'src': 3, 'dst': 0}, '4839d8', 'CMP RAX, RBX'),

        # INC/DEC
        ('inc', {'dst': 0}, '48ffc0', 'INC RAX'),
        ('dec', {'dst': 1}, '48ffc9', 'DEC RCX'),

        # NEG/NOT
        ('neg', {'dst': 0}, '48f7d8', 'NEG RAX'),
        ('not', {'dst': 0}, '48f7d0', 'NOT RAX'),

        # Shifts
        ('shl_ri', {'dst': 0, 'imm': 4}, '48c1e004', 'SHL RAX, 4'),
        ('shr_ri', {'dst': 0, 'imm': 1}, '48c1e801', 'SHR RAX, 1'),

        # CDQ/CQO (sign extend for division)
        ('cdq', {}, '99', 'CDQ'),
        ('cqo', {}, '4899', 'CQO'),

        # Conditional jumps (short form)
        ('je_rel8', {'offset': 0x10}, '7410', 'JE +16'),
        ('jne_rel8', {'offset': 0x05}, '7505', 'JNE +5'),
        ('jmp_rel8', {'offset': 0xFE}, 'ebfe', 'JMP -2 (infinite loop)'),

        # String ops
        ('rep_movsb', {}, 'f3a4', 'REP MOVSB'),
        ('rep_stosq', {}, 'f348ab', 'REP STOSQ'),

        # Bit operations (mod=11 for register-register, so 0xC1 not 0x01)
        ('bsf', {'dst': 0, 'src': 1}, '480fbcc1', 'BSF RAX, RCX'),
        ('bsr', {'dst': 0, 'src': 1}, '480fbdc1', 'BSR RAX, RCX'),

        # CMOV (mod=11 for register-register)
        ('cmove', {'dst': 0, 'src': 1}, '480f44c1', 'CMOVE RAX, RCX'),
        ('cmovne', {'dst': 0, 'src': 1}, '480f45c1', 'CMOVNE RAX, RCX'),

        # SET
        ('sete', {'dst': 0}, '0f94c0', 'SETE AL'),
        ('setne', {'dst': 0}, '0f95c0', 'SETNE AL'),

        # Prologue/Epilogue (compound)
        ('prologue', {}, '554889e5', 'PROLOGUE (push rbp; mov rbp, rsp)'),
        ('epilogue', {}, '5dc3', 'EPILOGUE (pop rbp; ret)'),
    ]

    passed = 0
    failed = 0

    for instruction, kwargs, expected_hex, description in tests:
        try:
            result = emit_instruction(traverser, instruction, **kwargs)
            result_hex = result.hex()

            if result_hex == expected_hex:
                print(f"✓ {description}: {result_hex}")
                passed += 1
            else:
                print(f"✗ {description}: got {result_hex}, expected {expected_hex}")
                failed += 1
        except Exception as e:
            print(f"✗ {description}: ERROR - {e}")
            failed += 1

    print(f"\n{passed}/{passed+failed} encoding tests passed")
    return failed == 0


def test_complex_program():
    """Generate and run a more complex program using BLD traverser."""
    print("\n=== Test: Complex Program (fibonacci mod 256) ===")

    # Load x86.bld
    with open('../bld/bootstrap/x86.bld') as f:
        x86_source = f.read()
    x86_struct = parse(x86_source)

    traverser = Traverser()
    traverser.state.structures['X86'] = x86_struct

    # Compute fib(10) mod 256 = 55
    # fib(0)=0, fib(1)=1, fib(2)=1, fib(3)=2, fib(4)=3, fib(5)=5,
    # fib(6)=8, fib(7)=13, fib(8)=21, fib(9)=34, fib(10)=55

    code = bytearray()

    def add_instr(instr, **kwargs):
        result = emit_instruction(traverser, instr, **kwargs)
        code.extend(result)

    # rax = 0 (fib(n-2))
    add_instr('xor_rr', src=0, dst=0)

    # rbx = 1 (fib(n-1))
    add_instr('mov_ri', dst_ext=0, dst_lo=3, imm=1)

    # rcx = 10 (counter)
    add_instr('mov_ri', dst_ext=0, dst_lo=1, imm=10)

    # loop:
    loop_start = len(code)

    # rdx = rax (save fib(n-2))
    add_instr('mov_rr', src=0, dst=2)  # mov rdx, rax

    # rax = rbx (fib(n-1) becomes new fib(n-2))
    add_instr('mov_rr', src=3, dst=0)  # mov rax, rbx

    # rbx = rbx + rdx (fib(n) = fib(n-1) + fib(n-2))
    add_instr('add_rr', src=2, dst=3)  # add rbx, rdx

    # dec rcx
    add_instr('dec', dst=1)

    # jne loop (offset calculated from end of jne)
    offset = loop_start - (len(code) + 2)  # +2 for jne instruction size
    add_instr('jne_rel8', offset=offset & 0xFF)

    # After 10 iterations: rax=fib(10)=55, rbx=fib(11)=89
    # mov rdi, rax (exit code = fib(10))
    add_instr('mov_rr', src=0, dst=7)

    # mov rax, 60 (exit syscall)
    add_instr('mov_ri', dst_ext=0, dst_lo=0, imm=60)

    # syscall
    add_instr('syscall')

    print(f"Generated {len(code)} bytes")
    print(f"Hex: {code.hex()}")

    elf = create_elf64(bytes(code))

    with tempfile.NamedTemporaryFile(delete=False, suffix='.elf') as f:
        f.write(elf)
        elf_path = f.name

    os.chmod(elf_path, 0o755)

    try:
        result = subprocess.run([elf_path], capture_output=True, timeout=5)
    except:
        result = subprocess.run(['qemu-x86_64', elf_path], capture_output=True, timeout=5)

    os.unlink(elf_path)

    print(f"Exit code: {result.returncode}")

    if result.returncode == 55:
        print("✓ PASS: fib(10) = 55!")
        return True
    else:
        print(f"✗ FAIL: expected 55, got {result.returncode}")
        return False


def create_elf64(code: bytes) -> bytes:
    """Create minimal ELF64 executable."""
    entry = 0x400078
    elf = bytearray()

    # ELF header
    elf.extend(b'\x7fELF')
    elf.append(2); elf.append(1); elf.append(1); elf.append(0)
    elf.extend(b'\x00' * 8)
    elf.extend((2).to_bytes(2, 'little'))
    elf.extend((0x3E).to_bytes(2, 'little'))
    elf.extend((1).to_bytes(4, 'little'))
    elf.extend(entry.to_bytes(8, 'little'))
    elf.extend((64).to_bytes(8, 'little'))
    elf.extend((0).to_bytes(8, 'little'))
    elf.extend((0).to_bytes(4, 'little'))
    elf.extend((64).to_bytes(2, 'little'))
    elf.extend((56).to_bytes(2, 'little'))
    elf.extend((1).to_bytes(2, 'little'))
    elf.extend((64).to_bytes(2, 'little'))
    elf.extend((0).to_bytes(2, 'little'))
    elf.extend((0).to_bytes(2, 'little'))

    # Program header
    file_size = 120 + len(code)
    elf.extend((1).to_bytes(4, 'little'))
    elf.extend((5).to_bytes(4, 'little'))
    elf.extend((0).to_bytes(8, 'little'))
    elf.extend((0x400000).to_bytes(8, 'little'))
    elf.extend((0x400000).to_bytes(8, 'little'))
    elf.extend(file_size.to_bytes(8, 'little'))
    elf.extend(file_size.to_bytes(8, 'little'))
    elf.extend((0x1000).to_bytes(8, 'little'))

    elf.extend(code)
    return bytes(elf)


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    results = []
    results.append(test_instruction_encodings())
    results.append(test_complex_program())

    print("\n" + "=" * 50)
    print(f"Results: {sum(results)}/{len(results)} test suites passed")

    sys.exit(0 if all(results) else 1)
