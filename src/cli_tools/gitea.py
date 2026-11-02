"""Cliente de línea de comandos para la API de Gitea, con login OAuth2 (PKCE) en el navegador."""
import argparse
import base64
import hashlib
import http.server
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import core
from .core import ToolError, HttpError, info

PROG = "gitea"
URL_VAR = "GITEA_URL"
# App OAuth2 pública que Gitea trae integrada para tea (redirige a 127.0.0.1, cualquier puerto)
DEFAULT_CLIENT_ID = "d57cb8c4-630c-4168-8324-ec79935e18d4"
DEFAULT_CALLBACK_PORT = 38291
# Renueva antes de que venza, para no gastar una petición en un 401
EXPIRY_MARGIN = 60


class Gitea:
    def __init__(self, url=None, require_url=True):
        self.conf_dir = Path(os.environ.get("GITEA_CONF_DIR") or Path.home() / ".config" / "gitea")
        self.credentials_file = self.conf_dir / "credentials.json"
        # CA adicional opcional (p. ej. una CA interna); si no existe se usa el almacén del sistema
        self.cacert = Path(os.environ.get("GITEA_CACERT") or self.conf_dir / "ca.pem")
        # Proveedor externo de Gitea (p. ej. un OIDC); vacío = página de login de Gitea
        self.auth_source = os.environ.get("GITEA_AUTH_SOURCE", "")
        self.client_id = os.environ.get("GITEA_CLIENT_ID", DEFAULT_CLIENT_ID)
        self.callback_port = int(os.environ.get("GITEA_CALLBACK_PORT", DEFAULT_CALLBACK_PORT))
        env_url = os.environ.get(URL_VAR, "").rstrip("/")
        self.url = core.resolve_url(url, env_url, self.credentials_file, PROG, URL_VAR) if require_url else None

    def request(self, method, path, body=None, token=None):
        return core.json_request(method, core.api_url(self.url, path), body, token, self.cacert)

    def token_request(self, fields):
        """POST al endpoint de tokens OAuth2 de Gitea (fuera de /api/v1/)."""
        req = urllib.request.Request(f"{self.url}/login/oauth/access_token",
                                     data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("Accept", "application/json")
        return core.send(req, self.cacert)

    # --- Credenciales ---

    def save_credentials(self, tokens, previous_refresh="", client_id=None):
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not isinstance(token, str) or not token:
            raise ToolError("La respuesta de autenticación no trae access_token")
        expires_in = tokens.get("expires_in")
        core.write_private_json(self.credentials_file, {
            "url": self.url,
            "client_id": client_id or self.client_id,
            "access_token": token,
            # Gitea puede invalidar el refresh al usarlo; siempre se guarda el último que devolvió
            "refresh_token": tokens.get("refresh_token") or previous_refresh,
            "expires_at": int(time.time()) + int(expires_in) if isinstance(expires_in, (int, float)) else None,
        })

    def load_credentials(self):
        creds = core.read_json_file(self.credentials_file)
        if creds is None and not self.credentials_file.exists():
            raise ToolError("No hay sesión; corre " + core.err.bold(f"{PROG} login"))
        if not isinstance(creds, dict) or not creds.get("access_token"):
            raise ToolError(f"{self.credentials_file} está incompleto; corre {PROG} login")
        if creds.get("url") != self.url:
            raise ToolError(f"La sesión guardada es de {creds.get('url')}, no de {self.url}; corre {PROG} login")
        return creds

    def refresh(self, creds):
        """True si renovó; False si el servidor rechazó el refresh. Otros fallos se propagan."""
        if not creds.get("refresh_token"):
            return False
        client_id = creds.get("client_id") or self.client_id
        try:
            _, raw, _ = self.token_request({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
                                            "client_id": client_id})
        except HttpError as e:
            if e.status in (400, 401, 403):
                return False
            raise
        self.save_credentials(core.parse_json(raw, "Renovar token"), creds["refresh_token"], client_id)
        return True

    def fresh_credentials(self):
        creds = self.load_credentials()
        expires_at = creds.get("expires_at")
        if isinstance(expires_at, int) and expires_at - EXPIRY_MARGIN < time.time() and self.refresh(creds):
            creds = self.load_credentials()
        return creds

    def call(self, method, path, body):
        creds = self.fresh_credentials()
        try:
            return self.request(method, path, body, creds["access_token"])
        except HttpError as e:
            if e.status != 401 or not self.refresh(creds):
                raise
            info(core.err.dim("El servidor respondió 401; token renovado y petición reintentada."))
            return self.request(method, path, body, self.load_credentials()["access_token"])


# --- Login en el navegador -----------------------------------------------------

class CallbackHandler(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        CallbackHandler.result = {k: v[0] for k, v in query.items()}
        page = ("Listo: ya puedes cerrar esta pestaña y volver a la terminal." if "code" in query
                else "No se pudo iniciar sesión: " + query.get("error_description", query["error"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8><title>{PROG} login</title><p>{page}</p>".encode())

    def log_message(self, *args):
        pass  # sin ruido en la terminal


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login_url(g, source, redirect_uri, state, challenge):
    authorize = "/login/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": g.client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    # Con un proveedor, entra directo por él; Gitea guarda redirect_to y vuelve al authorize de OAuth2
    if source:
        return f"{g.url}/user/oauth2/{urllib.parse.quote(source)}?redirect_to={urllib.parse.quote(authorize)}"
    return g.url + authorize


def wait_for_callback(port, timeout, url, open_browser):
    CallbackHandler.result = None
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError as e:
        raise ToolError(f"No se pudo escuchar en 127.0.0.1:{port} ({e}); usa GITEA_CALLBACK_PORT") from None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    info(core.err.cyan("Abre este enlace para iniciar sesión:"))
    info(url)
    if open_browser:
        webbrowser.open(url)
    try:
        deadline = time.time() + timeout
        while CallbackHandler.result is None and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
    if CallbackHandler.result is None:
        raise ToolError(f"No llegó la respuesta del navegador en {timeout} s")
    return CallbackHandler.result


def cmd_login(g, args):
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://127.0.0.1:{g.callback_port}"
    source = args.source if args.source is not None else g.auth_source
    result = wait_for_callback(g.callback_port, args.timeout, login_url(g, source, redirect_uri, state, challenge),
                               not args.no_browser)
    if result.get("state") != state:
        raise ToolError("La respuesta del navegador no corresponde a este login (state distinto)")
    if "code" not in result:
        raise ToolError("Gitea rechazó el login: " + result.get("error_description", result.get("error", "?")))
    try:
        _, raw, _ = g.token_request({"grant_type": "authorization_code", "code": result["code"],
                                     "redirect_uri": redirect_uri, "client_id": g.client_id, "code_verifier": verifier})
    except HttpError as e:
        raise ToolError(f"Error al obtener el token: {core.error_detail(e.body)}") from None
    g.save_credentials(core.parse_json(raw, "Iniciar sesión"))
    _, raw, _ = g.request("GET", "user", token=g.load_credentials()["access_token"])
    me = core.parse_json(raw, "Consultar usuario")
    info(core.err.green("✓ ") + f"Sesión iniciada en {g.url} como {core.err.bold(me.get('login', '?'))}")


def cmd_refresh(g, args):
    if not g.refresh(g.load_credentials()):
        raise ToolError("El servidor rechazó la renovación; corre " + core.err.bold(f"{PROG} login"))
    info(core.err.green("✓ ") + "Token renovado.")


def cmd_logout(g, args):
    g.credentials_file.unlink(missing_ok=True)
    info(core.err.green("✓ ") + "Sesión cerrada.")


def cmd_token(g, args):
    print(g.fresh_credentials()["access_token"])


def cmd_me(g, args):
    _, raw, _ = g.call("GET", "user", None)
    me = core.parse_json(raw, "Consultar usuario")
    if not isinstance(me, dict):
        raise ToolError("Respuesta inesperada de user")
    print(f"{core.out.bold(me.get('full_name') or me.get('login', ''))} ({core.out.cyan(me.get('login', '?'))}) en {g.url}")
    expires_at = g.load_credentials().get("expires_at")
    if isinstance(expires_at, int):
        print(core.out.dim(f"Token vigente hasta {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires_at))}"))
    print(core.out.dim(f"Credenciales: {g.credentials_file}"))


def build_parser(prog=PROG):
    epilog = f"""\
ejemplos:
  {prog} login --url https://gitea.example.com       # la primera vez; abre el navegador
  {prog} login --source <proveedor>                   # entra directo por un proveedor externo de Gitea (OIDC)
  {prog} me
  {prog} GET "repos/<owner>/<repo>/pulls?state=open"
  {prog} POST repos/<owner>/<repo>/pulls -d @pr.json
  {prog} PATCH repos/<owner>/<repo>/pulls/<n> -d '{{"title": "Nuevo título"}}'
  export GITEA_TOKEN="$({prog} token)"               # para otros scripts

variables de entorno:
  GITEA_URL            servidor, solo https; si falta, el de la última sesión
  GITEA_AUTH_SOURCE    proveedor externo de Gitea; vacío = página de login de Gitea
  GITEA_CLIENT_ID      app OAuth2 pública de Gitea (por defecto la integrada para tea)
  GITEA_CALLBACK_PORT  puerto local para la respuesta del navegador (por defecto {DEFAULT_CALLBACK_PORT})
  GITEA_CONF_DIR       carpeta de credenciales (por defecto ~/.config/gitea)
  GITEA_CACERT         CA adicional (por defecto ca.pem en la carpeta de credenciales)
  NO_COLOR             desactiva los colores
"""
    parser = argparse.ArgumentParser(prog=prog, description="Cliente de la API de Gitea.", epilog=epilog,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", metavar="COMANDO")

    p = sub.add_parser("login", help="inicia sesión en el navegador (OAuth2 + PKCE) y guarda las credenciales")
    p.add_argument("--url", help="servidor de Gitea, p. ej. https://gitea.example.com")
    p.add_argument("--source", help="proveedor externo de Gitea por el que entrar directo (GITEA_AUTH_SOURCE)")
    p.add_argument("--no-browser", action="store_true", help="no abre el navegador; solo imprime el enlace")
    p.add_argument("--timeout", type=int, default=300, help="segundos de espera por el navegador (300)")
    p.set_defaults(func=cmd_login)

    sub.add_parser("refresh", help="renueva el token sin abrir el navegador").set_defaults(func=cmd_refresh)
    sub.add_parser("logout", help="borra las credenciales guardadas").set_defaults(func=cmd_logout)
    sub.add_parser("me", help="muestra el usuario con sesión iniciada").set_defaults(func=cmd_me)
    sub.add_parser("token", help="imprime el token de acceso (lo renueva si venció)").set_defaults(func=cmd_token)
    core.add_api_parser(sub, prog, "repos/<owner>/<repo>/pulls", lambda g, args: core.run_api(args, g.call))
    return parser


def main(argv, prog=PROG):
    parser = build_parser(prog)
    args = parser.parse_args(core.with_api_shortcut(argv))
    if not args.command:
        parser.print_help()
        return 2
    # logout solo borra el archivo: no necesita saber el servidor
    return args.func(Gitea(getattr(args, "url", None), require_url=args.command != "logout"), args)


def run():
    return core.run(main)
