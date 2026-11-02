"""Cliente de línea de comandos para la API de Taiga."""
import argparse
import getpass
import json
import os
from pathlib import Path

from . import core
from .core import ToolError, HttpError, info

PROG = "taiga"
URL_VAR = "TAIGA_URL"


class Taiga:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("TAIGA_CONF_DIR") or Path.home() / ".config" / "taiga")
        self.credentials_file = self.conf_dir / "credentials.json"
        # CA adicional opcional (p. ej. una CA interna); si no existe se usa el almacén del sistema
        self.cacert = Path(os.environ.get("TAIGA_CACERT") or self.conf_dir / "ca.pem")
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = core.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return core.json_request(method, core.api_url(self.url, path), body, token, self.cacert)

    # --- Credenciales ---

    def save_credentials(self, auth, previous_refresh=""):
        token = auth.get("auth_token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("La respuesta de autenticación no trae auth_token")
        core.write_private_json(self.credentials_file,
                                {"url": self.url, "auth_token": token, "refresh": auth.get("refresh") or previous_refresh})

    def load_credentials(self):
        creds = core.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise ToolError("No hay sesión; corre " + core.err.bold(f"{PROG} login"))
        if not isinstance(creds, dict) or not creds.get("auth_token"):
            raise ToolError(f"{self.credentials_file} está incompleto; corre {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"La sesión guardada es de {creds.get('url')}, no de {self.url}; corre {PROG} login")
        return creds

    def refresh(self, creds):
        """True si renovó; False si el servidor rechazó el refresh. Otros fallos se propagan."""
        if not creds.get("refresh"):
            return False
        try:
            _, raw, _ = self.request("POST", "auth/refresh", json.dumps({"refresh": creds["refresh"]}))
        except HttpError as e:
            if e.status in (400, 401, 403):
                return False
            raise
        self.save_credentials(core.parse_json(raw, "Renovar token"), creds["refresh"])
        return True

    def call(self, method, path, body):
        creds = self.load_credentials()
        try:
            return self.request(method, path, body, creds["auth_token"])
        except HttpError as e:
            if e.status != 401 or not self.refresh(creds):
                raise
            info(core.err.dim("El servidor respondió 401; token renovado y petición reintentada."))
            return self.request(method, path, body, self.load_credentials()["auth_token"])


# --- Comandos -----------------------------------------------------------------

def cmd_login(t, args):
    user = args.user or input(core.err.cyan("Usuario o correo: "))
    password = getpass.getpass(core.err.cyan("Contraseña: "))
    body = json.dumps({"type": "normal", "username": user, "password": password})
    try:
        _, raw, _ = t.request("POST", "auth", body)
    except HttpError as e:
        raise ToolError(f"Error al iniciar sesión: {core.error_detail(e.body)}") from None
    data = core.parse_json(raw, "Iniciar sesión")
    t.save_credentials(data)
    info(core.err.green("✓ ") + f"Sesión iniciada en {t.url} como {core.err.bold(data.get('username', user))}")


def cmd_refresh(t, args):
    if not t.refresh(t.load_credentials()):
        raise ToolError("El servidor rechazó la renovación; corre " + core.err.bold(f"{PROG} login"))
    info(core.err.green("✓ ") + "Token renovado.")


def cmd_logout(t, args):
    t.credentials_file.unlink(missing_ok=True)
    info(core.err.green("✓ ") + "Sesión cerrada.")


def cmd_token(t, args):
    print(t.load_credentials()["auth_token"])


def cmd_me(t, args):
    _, raw, _ = t.call("GET", "users/me", None)
    me = core.parse_json(raw, "Consultar usuario")
    if not isinstance(me, dict):
        raise ToolError("Respuesta inesperada de users/me")
    print(f"{core.out.bold(me.get('full_name_display', ''))} ({core.out.cyan(me.get('username', '?'))}) en {t.url}")
    print(core.out.dim(f"Credenciales: {t.credentials_file}"))


def build_parser(prog=PROG):
    epilog = f"""\
ejemplos:
  {prog} login --url https://taiga.example.com      # la primera vez; después basta con {prog} login
  {prog} me
  {prog} GET "userstories?project=<id>"
  {prog} GET "userstories?project=<id>&page=2" -i   # ver cabeceras x-pagination-*
  {prog} PATCH userstories/123 -d '{{"version": 4, "subject": "Nuevo título"}}'
  {prog} POST tasks -d @tarea.json
  export TAIGA_TOKEN="$({prog} token)"               # para otros scripts

variables de entorno:
  TAIGA_URL         servidor, solo https; si falta, el de la última sesión
  TAIGA_CONF_DIR    carpeta de credenciales (por defecto ~/.config/taiga)
  TAIGA_CACERT      CA adicional (por defecto ca.pem en la carpeta de credenciales)
  NO_COLOR          desactiva los colores
"""
    parser = argparse.ArgumentParser(prog=prog, description="Cliente de la API de Taiga.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMANDO")

    p = sub.add_parser("login", help="inicia sesión y guarda las credenciales")
    p.add_argument("-u", "--user", help="usuario o correo (si falta, se pregunta)")
    p.add_argument("--url", help="servidor de Taiga, p. ej. https://taiga.example.com")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", help="renueva el token sin pedir contraseña").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", help="borra las credenciales guardadas").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="muestra el usuario con sesión iniciada").set_defaults(func=cmd_me)
    sub.add_parser("token", help="imprime el token de acceso").set_defaults(func=cmd_token)
    core.add_api_parser(sub, prog, "userstories?project=<id>", lambda t, args: core.run_api(args, t.call))
    return parser


def main(argv, prog=PROG):
    parser = build_parser(prog)
    args = parser.parse_args(core.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    # logout solo borra el archivo: no necesita saber el servidor
    return args.func(Taiga(getattr(args, "url", None), require_url=args.command != "logout"), args)


def run():
    return core.run(main)
