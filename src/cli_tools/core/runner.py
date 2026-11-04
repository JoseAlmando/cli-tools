"""Runs a command's main() and turns expected errors into messages and exit codes."""
import json
import sys

from cli_tools.core import output
from cli_tools.core.context import parse_global_flags
from cli_tools.core.errors import ToolError


def run(main, argv=None):
    """Runs main(argv, ctx=...) with the global flags taken out of argv, and turns expected errors into exit codes."""
    output.setup_streams()
    ctx, argv = parse_global_flags(sys.argv[1:] if argv is None else argv)
    try:
        return main(argv, ctx=ctx) or 0
    except ToolError as e:
        if ctx.json_mode:
            print(json.dumps({"error": str(e), "type": type(e).__name__, "exit_code": e.exit_code,
                              "status": getattr(e, "status", None)}, ensure_ascii=False), file=sys.stderr)
        else:
            output.info(output.err.red("✗ ") + str(e))
        return e.exit_code
    except BrokenPipeError:
        # e.g. "clt taiga GET ... | head": not an error
        sys.stderr.close()
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
