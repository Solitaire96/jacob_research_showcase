"""Test-wide settings: never read or write the real template library."""
import os
import tempfile

os.environ["TEMPLATE_INDEX_PATH"] = os.path.join(tempfile.mkdtemp(), "line_index.json")
