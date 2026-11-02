"""clt: un solo comando para todas las herramientas (clt taiga ..., clt gitea ...)."""
import sys

from . import __version__, core, gitea, taiga

TOOLS = {
    "taiga": (taiga.main, "cliente de la API de Taiga"),
    "gitea": (gitea.main, "cliente de la API de Gitea, con login OAuth2 en el navegador"),
}


def usage():
    lines = ["uso: clt HERRAMIENTA [argumentos]", "", "herramientas:"]
    lines += [f"  {name:<8} {description}" for name, (_, description) in TOOLS.items()]
    lines += ["", "Cada herramienta tiene su ayuda: clt taiga --help, clt gitea --help.",
              "También se instalan sueltas: taiga ..., gitea ..."]
    return "\n".join(lines)


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        print(f"clt {__version__}")
        return 0
    tool = TOOLS.get(argv[0])
    if tool is None:
        print(usage(), file=sys.stderr)
        raise core.ToolError(f"No conozco la herramienta {argv[0]!r}")
    return tool[0](argv[1:], prog=f"clt {argv[0]}")


def run():
    return core.run(main)
