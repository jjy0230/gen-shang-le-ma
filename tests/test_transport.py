"""Exercise production browser transport functions with deterministic faults."""
import shutil
import subprocess

import pytest


def test_transport_failure_invariants():
    if not shutil.which('node'):
        pytest.skip('Node is required for browser transport unit checks')
    subprocess.run(['node', 'scripts/test_transport.cjs'], check=True)
