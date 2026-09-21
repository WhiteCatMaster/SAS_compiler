from .macro import MacroProcessor
from .parser import parse
from .codegen import generate

__version__ = "0.1.0"


def compile_source(source: str, nested: bool = False) -> str:
    """Compile SAS source to Python source. `nested=True` is used by
    CALL EXECUTE's runtime drain: it skips the _DS/_FMT/_LBL/_TITLES/
    _FOOTNOTES initializer lines, since the caller injects those objects
    (shared with the outer program) directly into the exec() namespace."""
    expanded = MacroProcessor().expand(source)
    prog = parse(expanded)
    return generate(prog, nested=nested)


__all__ = ["MacroProcessor", "parse", "generate", "compile_source", "__version__"]
