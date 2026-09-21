import argparse
import sys

from . import compile_source
from .macro import MacroProcessor


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sasc", description="Compile SAS source to Python (pandas/duckdb)")
    ap.add_argument("input", help="SAS source file")
    ap.add_argument("-o", "--output", help="write generated Python to this file")
    ap.add_argument("--run", action="store_true", help="execute the generated Python immediately")
    ap.add_argument("--emit-macro", action="store_true", help="print macro-expanded SAS and exit (no compile)")
    args = ap.parse_args(argv)

    with open(args.input) as f:
        source = f.read()

    if args.emit_macro:
        print(MacroProcessor().expand(source))
        return 0

    code = compile_source(source)

    if args.output:
        with open(args.output, "w") as f:
            f.write(code)
        print(f"wrote {args.output}", file=sys.stderr)

    if args.run or not args.output:
        g = {"__name__": "__main__", "__file__": args.input}
        exec(compile(code, args.input, "exec"), g)

    return 0


if __name__ == "__main__":
    sys.exit(main())
