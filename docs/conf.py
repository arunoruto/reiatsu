"""Sphinx configuration for the Reiatsu docs."""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

project = "Reiatsu"
author = "Mirza Arnaut"
copyright = "2026, Mirza Arnaut"
release = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.mathjax",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]
myst_enable_extensions = ["dollarmath", "amsmath", "colon_fence", "deflist"]
myst_heading_anchors = 3
myst_fence_as_directive = ["mermaid"]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_mock_imports = ["paho"]
napoleon_numpy_docstring = True

html_theme = "shibuya"
html_title = f"Reiatsu {release}"
html_theme_options = {
    "github_url": "https://github.com/arunoruto/reiatsu",
    "nav_links": [{"title": "Releases", "url": "https://github.com/arunoruto/reiatsu/releases"}],
}
exclude_patterns = ["_build"]
