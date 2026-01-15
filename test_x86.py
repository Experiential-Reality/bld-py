#!/usr/bin/env python3
"""Test x86.bld by generating and executing real x86-64 code."""

import sys
import os
import tempfile
import subprocess

sys.path.insert(0, 'src')

from bld_py.parser import parse
from bld_py.traverser import Traverser, State


def test_exit_42():
    """Generate exit(42) program and verify execution."""
    print("=== Test: exit(42) ===")

    # Load x86.bld
    with open('../bld/bootstrap/x86.bld') as f:
        x86_source = f.read()
    x86_struct = parse(x86_source)

    # Create traverser and emit instructions manually
    state = State()

    # exit(42):
    #   mov rax, 60    ; syscall number
    #   mov rdi, 42    ; exit code
    #   syscall

    # MOV RAX, 60: REX.W B8+0 imm64
    # REX.W = 0x48, B8+0 = 0xB8
    state.emit_byte(0x48)  # REX.W
    state.emit_byte(0xB8)  # MOV RAX, imm64
    state.emit_u64(60)     # syscall number for exit

    # MOV RDI, 42: REX.W B8+7 imm64
    state.emit_byte(0x48)  # REX.W
    state.emit_byte(0xBF)  # MOV RDI, imm64 (B8+7)
    state.emit_u64(42)     # exit code

    # SYSCALL: 0F 05
    state.emit_bytes(0x0F, 0x05)

    code = bytes(state.output)
    print(f"Generated {len(code)} bytes of x86 code")
    print(f"Hex: {code.hex()}")

    # Create minimal ELF64 executable
    elf = create_elf64(code)
    print(f"ELF size: {len(elf)} bytes")

    # Write and execute
    with tempfile.NamedTemporaryFile(delete=False, suffix='.elf') as f:
        f.write(elf)
        elf_path = f.name

    os.chmod(elf_path, 0o755)

    # Try direct execution first, fall back to QEMU
    try:
        result = subprocess.run([elf_path], capture_output=True, timeout=5)
        exit_code = result.returncode
        method = "direct"
    except Exception as e:
        # Try QEMU
        try:
            result = subprocess.run(['qemu-x86_64', elf_path], capture_output=True, timeout=5)
            exit_code = result.returncode
            method = "qemu-x86_64"
        except FileNotFoundError:
            print("Neither direct execution nor QEMU available")
            os.unlink(elf_path)
            return False

    os.unlink(elf_path)

    print(f"Execution method: {method}")
    print(f"Exit code: {exit_code}")

    if exit_code == 42:
        print("✓ PASS: exit(42) works!")
        return True
    else:
        print(f"✗ FAIL: expected 42, got {exit_code}")
        return False


def test_arithmetic():
    """Test arithmetic operations: compute 10 + 32 = 42, exit with result."""
    print("\n=== Test: arithmetic (10 + 32 = 42) ===")

    state = State()

    # mov rax, 10
    state.emit_byte(0x48); state.emit_byte(0xB8); state.emit_u64(10)

    # mov rbx, 32
    state.emit_byte(0x48); state.emit_byte(0xBB); state.emit_u64(32)

    # add rax, rbx: REX.W 01 /r (mod=11, reg=rbx=3, rm=rax=0)
    state.emit_byte(0x48)  # REX.W
    state.emit_byte(0x01)  # ADD r/m64, r64
    state.emit_byte(0xD8)  # ModRM: 11 011 000 = 0xD8 (rbx -> rax)

    # mov rdi, rax: REX.W 89 /r
    state.emit_byte(0x48)
    state.emit_byte(0x89)
    state.emit_byte(0xC7)  # ModRM: 11 000 111 (rax -> rdi)

    # mov rax, 60 (exit syscall)
    state.emit_byte(0x48); state.emit_byte(0xB8); state.emit_u64(60)

    # syscall
    state.emit_bytes(0x0F, 0x05)

    code = bytes(state.output)
    print(f"Generated {len(code)} bytes")

    elf = create_elf64(code)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.elf') as f:
        f.write(elf)
        elf_path = f.name

    os.chmod(elf_path, 0o755)

    try:
        result = subprocess.run([elf_path], capture_output=True, timeout=5)
        exit_code = result.returncode
    except:
        result = subprocess.run(['qemu-x86_64', elf_path], capture_output=True, timeout=5)
        exit_code = result.returncode

    os.unlink(elf_path)

    print(f"Exit code: {exit_code}")
    if exit_code == 42:
        print("✓ PASS: 10 + 32 = 42!")
        return True
    else:
        print(f"✗ FAIL: expected 42, got {exit_code}")
        return False


def test_using_traverser():
    """Test using the actual BLD traverser with x86.bld structure."""
    print("\n=== Test: BLD traverser with x86.bld ===")

    # Load x86.bld
    with open('../bld/bootstrap/x86.bld') as f:
        x86_source = f.read()
    x86_struct = parse(x86_source)

    # Create traverser
    traverser = Traverser()
    traverser.state.structures['X86'] = x86_struct

    # Set up for mov_ri instruction (mov rax, 60)
    traverser.state.set('mode_instruction', 'mov_ri')
    traverser.state.set('dst_ext', 0)  # rax extension bit
    traverser.state.set('dst_lo', 0)   # rax low bits
    traverser.state.set('imm', 60)     # immediate value

    # Traverse to emit the instruction
    traverser.traverse(x86_struct)

    code1 = bytes(traverser.state.output)
    print(f"mov_ri emitted: {code1.hex()}")

    # Expected: 48 B8 3C 00 00 00 00 00 00 00
    expected = bytes([0x48, 0xB8, 60, 0, 0, 0, 0, 0, 0, 0])

    if code1 == expected:
        print("✓ PASS: mov_ri encoding correct!")
        return True
    else:
        print(f"✗ FAIL: expected {expected.hex()}, got {code1.hex()}")
        return False


def test_hello_world():
    """Test write syscall with hello world."""
    print("\n=== Test: Hello World (write syscall) ===")

    state = State()

    # The message will be embedded after the code, we'll compute the address
    message = b"Hello from BLD x86!\n"

    # Code starts at 0x400000 + 0x78 (ELF header size)
    code_start = 0x400078

    # We need to:
    # 1. Calculate where message will be
    # 2. Emit code
    # 3. Emit message

    # Emit code first to know its size, then adjust
    code_bytes = bytearray()

    # mov rax, 1 (write syscall)
    code_bytes.extend([0x48, 0xB8]); code_bytes.extend((1).to_bytes(8, 'little'))

    # mov rdi, 1 (stdout)
    code_bytes.extend([0x48, 0xBF]); code_bytes.extend((1).to_bytes(8, 'little'))

    # mov rsi, <message_addr> - placeholder, will fix
    msg_mov_offset = len(code_bytes)
    code_bytes.extend([0x48, 0xBE]); code_bytes.extend((0).to_bytes(8, 'little'))

    # mov rdx, <length>
    code_bytes.extend([0x48, 0xBA]); code_bytes.extend(len(message).to_bytes(8, 'little'))

    # syscall
    code_bytes.extend([0x0F, 0x05])

    # mov rax, 60 (exit)
    code_bytes.extend([0x48, 0xB8]); code_bytes.extend((60).to_bytes(8, 'little'))

    # mov rdi, 0 (exit code)
    code_bytes.extend([0x48, 0xBF]); code_bytes.extend((0).to_bytes(8, 'little'))

    # syscall
    code_bytes.extend([0x0F, 0x05])

    # Now we know code size, message comes right after
    message_addr = code_start + len(code_bytes)

    # Fix the message address in mov rsi
    code_bytes[msg_mov_offset+2:msg_mov_offset+10] = message_addr.to_bytes(8, 'little')

    # Append message
    code_bytes.extend(message)

    print(f"Code + data: {len(code_bytes)} bytes")
    print(f"Message at: 0x{message_addr:x}")

    elf = create_elf64(bytes(code_bytes))

    with tempfile.NamedTemporaryFile(delete=False, suffix='.elf') as f:
        f.write(elf)
        elf_path = f.name

    os.chmod(elf_path, 0o755)

    try:
        result = subprocess.run([elf_path], capture_output=True, timeout=5)
    except:
        result = subprocess.run(['qemu-x86_64', elf_path], capture_output=True, timeout=5)

    os.unlink(elf_path)

    output = result.stdout.decode()
    print(f"Output: {repr(output)}")
    print(f"Exit code: {result.returncode}")

    if output == message.decode() and result.returncode == 0:
        print("✓ PASS: Hello World works!")
        return True
    else:
        print("✗ FAIL")
        return False


def create_elf64(code: bytes) -> bytes:
    """Create minimal ELF64 executable."""

    # ELF64 header (64 bytes) + Program header (56 bytes) = 120 bytes (0x78)
    entry = 0x400078  # Entry point right after headers

    elf = bytearray()

    # ELF header
    elf.extend(b'\x7fELF')           # Magic
    elf.append(2)                     # 64-bit
    elf.append(1)                     # Little endian
    elf.append(1)                     # ELF version
    elf.append(0)                     # System V ABI
    elf.extend(b'\x00' * 8)          # Padding
    elf.extend((2).to_bytes(2, 'little'))    # ET_EXEC
    elf.extend((0x3E).to_bytes(2, 'little')) # x86-64
    elf.extend((1).to_bytes(4, 'little'))    # ELF version
    elf.extend(entry.to_bytes(8, 'little'))  # Entry point
    elf.extend((64).to_bytes(8, 'little'))   # Program header offset
    elf.extend((0).to_bytes(8, 'little'))    # Section header offset
    elf.extend((0).to_bytes(4, 'little'))    # Flags
    elf.extend((64).to_bytes(2, 'little'))   # ELF header size
    elf.extend((56).to_bytes(2, 'little'))   # Program header size
    elf.extend((1).to_bytes(2, 'little'))    # Number of program headers
    elf.extend((64).to_bytes(2, 'little'))   # Section header size
    elf.extend((0).to_bytes(2, 'little'))    # Number of section headers
    elf.extend((0).to_bytes(2, 'little'))    # Section name string table index

    # Program header (PT_LOAD)
    file_size = 120 + len(code)
    elf.extend((1).to_bytes(4, 'little'))    # PT_LOAD
    elf.extend((5).to_bytes(4, 'little'))    # Flags: R+X
    elf.extend((0).to_bytes(8, 'little'))    # Offset in file
    elf.extend((0x400000).to_bytes(8, 'little'))  # Virtual address
    elf.extend((0x400000).to_bytes(8, 'little'))  # Physical address
    elf.extend(file_size.to_bytes(8, 'little'))   # File size
    elf.extend(file_size.to_bytes(8, 'little'))   # Memory size
    elf.extend((0x1000).to_bytes(8, 'little'))    # Alignment

    # Code
    elf.extend(code)

    return bytes(elf)


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    results = []
    results.append(test_exit_42())
    results.append(test_arithmetic())
    results.append(test_using_traverser())
    results.append(test_hello_world())

    print("\n" + "=" * 50)
    print(f"Results: {sum(results)}/{len(results)} tests passed")

    if all(results):
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)
