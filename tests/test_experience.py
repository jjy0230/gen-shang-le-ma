import shutil
import subprocess
import pytest

def test_presentation_state_invariants():
    if not shutil.which('node'):pytest.skip('Node is needed for presentation logic checks')
    subprocess.run(['node','scripts/test_experience.cjs'],check=True)
