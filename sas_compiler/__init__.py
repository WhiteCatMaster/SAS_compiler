from .macro import MacroProcessor
from .parser import parse
from .codegen import generate

__version__ = "0.1.0"


def compile_source(source: str) -> str:
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    return generate(prog)


__all__ = ["MacroProcessor", "parse", "generate", "compile_source", "__version__"]
