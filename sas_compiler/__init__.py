from .macro import MacroProcessor
from .parser import parse
from .codegen import generate


def compile_source(source: str) -> str:
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    return generate(prog)


__all__ = ["MacroProcessor", "parse", "generate", "compile_source"]
