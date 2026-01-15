# BLD Bootstrap - Load and compose BLD structures
#
# This module loads all bootstrap .bld files and makes them available
# for composition via the uses= attribute.
#
# The Three Questions applied to Bootstrap:
#   Q1: Where does bootstrap partition? → file loading | structure registration | composition
#   Q2: What connects? → file → parse → structure → registry
#   Q3: What repeats? → D files: N [parallel]

import os
from pathlib import Path
from typing import Dict, Optional
from .parser import parse, ParsedStructure


def get_bld_repo() -> Path:
    """Get the BLD repo path from environment or default."""
    env_path = os.environ.get('BLD_REPO')
    if env_path:
        return Path(env_path)
    # Default: sibling directory to bld-py
    return Path(__file__).parent.parent.parent.parent / 'bld'


def get_bootstrap_dir() -> Path:
    """Get the bootstrap directory path."""
    return get_bld_repo() / 'bootstrap'


def get_examples_dir() -> Path:
    """Get the examples directory path."""
    return get_bld_repo() / 'examples'


def load_bootstrap(bootstrap_dir: Optional[Path] = None) -> Dict[str, ParsedStructure]:
    """Load all bootstrap structures for composition.

    BLD structure:
        D files: N [parallel]
        L load: file -> structure (deps=0)
        L register: structure -> registry (deps=1)

    Returns:
        Dict mapping structure name to ParsedStructure
    """
    if bootstrap_dir is None:
        bootstrap_dir = get_bootstrap_dir()

    structures = {}

    if not bootstrap_dir.exists():
        return structures

    # D files: N [parallel] - load all .bld files
    for bld_file in bootstrap_dir.glob('*.bld'):
        try:
            with open(bld_file) as f:
                source = f.read()
            struct = parse(source)
            if struct and struct.name:
                structures[struct.name] = struct
        except Exception as e:
            # Log but continue - don't fail on one bad file
            print(f"Warning: Failed to load {bld_file}: {e}")

    return structures


def load_structure(path: Path) -> Optional[ParsedStructure]:
    """Load a single BLD structure from file.

    L load: path -> structure (deps=0)
    """
    try:
        with open(path) as f:
            source = f.read()
        return parse(source)
    except Exception as e:
        print(f"Error loading {path}: {e}")
        return None
