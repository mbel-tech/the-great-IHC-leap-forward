"""The tutorial's hooks inside the ROI curator page, under Node against a stub page.

The page suites (tests/*.test.js) need a real study's worklist to build the
page; these hooks are injected into it at run time instead, so they are tested
on their own here. Skips when node is not installed.

    python tests/test_tutorial_curator.py
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

node = shutil.which("node")
if not node:
    print("SKIP node is not installed - the curator hook suite needs it")
    sys.exit(0)
sys.exit(subprocess.call([node, os.path.join(HERE, "tutorial_curator_hooks.js")]))
