"""Shared Ren'Py semantic services used by the host and injected runtime."""

KERNEL_API_VERSION = "2"
STORYGRAPH_SCHEMA_VERSION = 3
RUNTIME_PROTOCOL_VERSION = 3

from .graph_machine import predict
from .model import (BlockIR, CallArgumentIR, CallSiteIR, EdgeIR, EdgeRef, EffectIR,
                    ExprIR, FormalBindingIR, GraphIR, NodeIR, NodeRef, PrecisionSet,
                    ScreenActionIR, ScreenAnalysis, ScreenIR, SourceLocation, SymbolRef)
from .serialization import dump_graph, load_graph, semantic_view

__all__ = [
    "KERNEL_API_VERSION",
    "STORYGRAPH_SCHEMA_VERSION",
    "RUNTIME_PROTOCOL_VERSION",
    "predict",
    "SourceLocation", "SymbolRef", "ExprIR", "EffectIR", "NodeRef", "EdgeRef",
    "CallArgumentIR", "FormalBindingIR", "CallSiteIR",
    "PrecisionSet", "NodeIR", "EdgeIR", "BlockIR", "GraphIR",
    "ScreenActionIR", "ScreenIR", "ScreenAnalysis",
    "load_graph", "dump_graph", "semantic_view",
]
