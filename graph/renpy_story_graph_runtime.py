"""Compatibility command wrapper for the StoryGraph Kernel builder."""

import os
import sys

import renpy

semantics_dir = os.environ.get("RENPYLENS_SEMANTICS_DIR")
if not semantics_dir:
    semantics_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if semantics_dir and semantics_dir not in sys.path:
    sys.path.insert(0, semantics_dir)

from renpylens_semantics import graph_builder as _kernel_builder

_python_semantics = _kernel_builder._python_semantics
_build_narrative_blocks = _kernel_builder._build_narrative_blocks
_node_ids = _kernel_builder._node_ids
_node_semantics = _kernel_builder._node_semantics
_audit_graph = _kernel_builder._audit_graph
run = _kernel_builder.run

renpy.arguments.register_command("renpylens_story_graph", run)
