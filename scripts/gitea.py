#!/usr/bin/env python3
"""Cliente de línea de comandos para la API de Gitea, con login OAuth2 (PKCE) en el navegador."""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

CONF_DIR = Path(os.environ.get("GITEA_CONF_DIR") or Path.home() / ".config" / "gitea")
ENV_URL = os.environ.get("GITEA_URL", "").rstrip("/")
URL = ""  # main() lo resuelve: --url, GITEA_URL o el servidor de la última sesión
# Proveedor externo de Gitea (p. ej. un OIDC); vacío = página de login de Gitea
AUTH_SOURCE = os.environ.get("GITEA_AUTH_SOURCE", "")
# App OAuth2 pública: por defecto la integrada de Gitea para tea (redirige a 127.0.0.1, cualquier puerto)
CLIENT_ID = os.environ.get("GITEA_CLIENT_ID", "d57cb8c4-630c-4168-8324-ec79935e18d4")
CALLBACK_PORT = int(os.environ.get("GITEA_CALLBACK_PORT", "38291"))
# CA adicional opcional (p. ej. una CA interna); si no existe se usa el almacén del sistema
CACERT = Path(os.environ.get("GITEA_CACERT") or CONF_DIR / "ca.pem")
CREDENTIALS_FILE = CONF_DIR / "credentials.json"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
# Renueva antes de que venza, para no gastar una petición en un 401
EXPIRY_MARGIN = 60


class GiteaError(Exception):
    """Error esperable; main() lo muestra sin traceback."""


class HttpError(GiteaError):
    def __init__(self, status, body, headers=None):
        super().__init__(f"HTTP {status}: {error_detail(body)}")
        self.status = status
        self.body = body
        self.headers = headers or {}


class Color:
    def __init__(self, stream):
        isatty = getattr(stream, "isatty", None)
        self.enabled = bool(isatty and isatty()) and not os.environ.get("NO_COLOR")
        if self.enabled and os.name == "nt":
            os.system("")  # activa las secuencias ANSI en la consola de Windows

    def paint(self, code, text):
        return f"\033[{code}m{text}\033[0m" if self.enabled else str(text)

    def red(self, t): return self.paint("31", t)
    def green(self, t): return self.paint("32", t)
    def yellow(self, t): return self.paint("33", t)
    def blue(self, t): return self.paint("34", t)
    def cyan(self, t): return self.paint("36", t)
    def dim(self, t): return self.paint("2", t)
    def bold(self, t): return self.paint("1", t)


out = Color(None)
err = Color(None)


def setup_streams():
    global out, err
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if encoding and encoding.lower() != "utf-8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    out = Color(sys.stdout)
    err = Color(sys.stderr)


def info(msg):
    print(msg, file=sys.stderr)


# --- HTTP -------------------------------------------------------------------

def validate_base_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise GiteaError(f"GITEA_URL debe ser https://servidor (es {url!r})")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise GiteaError(f"GITEA_URL no puede llevar usuario, query ni fragmento ({url!r})")
    return url.rstrip("/")


def resolve_url(cli_url=None):
    """Servidor: --url de login, si no la variable de entorno, si no el de la última sesión."""
    if cli_url:
        return validate_base_url(cli_url)
    if ENV_URL:
        return validate_base_url(ENV_URL)
    try:
        saved = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8")).get("url")
    except (OSError, ValueError, AttributeError):
        saved = None
    if not saved:
        raise GiteaError("No hay servidor configurado; usa gitea login --url https://servidor o define GITEA_URL")
    return validate_base_url(saved)


def api_url(base, path):
    """Solo rutas relativas a la API: el token nunca sale del servidor configurado."""
    if "://" in path or path.startswith("//") or "\\" in path:
        raise GiteaError(f"Solo se aceptan rutas relativas a {base}/api/v1/ (recibí {path!r})")
    return f"{base}/api/v1/{path.lstrip('/')}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib reenvía Authorization al seguir redirecciones; no las seguimos
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ssl_context():
    ctx = ssl.create_default_context()
    if CACERT.is_file():
        try:
            ctx.load_verify_locations(str(CACERT))
        except ssl.SSLError as e:
            raise GiteaError(f"El certificado {CACERT} no es válido: {e}") from None
    return ctx


def send(req):
    """Devuelve (status, cuerpo, cabeceras). Lanza HttpError o GiteaError; nunca termina el programa."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), dict(e.headers)) from None
    except urllib.error.URLError as e:
        raise GiteaError(f"Sin conexión con {URL}: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise GiteaError(f"Fallo de red con {URL}: {e}") from None


def request(method, path, body=None, token=None):
    req = urllib.request.Request(api_url(validate_base_url(URL), path),
                                 data=body.encode("utf-8") if isinstance(body, str) else body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return send(req)


def token_request(fields):
    """POST al endpoint de tokens OAuth2 de Gitea (fuera de /api/v1/)."""
    req = urllib.request.Request(f"{validate_base_url(URL)}/login/oauth/access_token",
                                 data=urllib.parse.urlencode(fields).encode("ascii"), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    return send(req)


def error_detail(body):
    """Siempre devuelve texto, sea cual sea la forma de la respuesta."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return " ".join(str(body).split())[:200]
    if isinstance(data, dict):
        data = data.get("message") or data.get("error_description") or data.get("error") or data
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError:
        raise GiteaError(f"{what}: el servidor no devolvió JSON ({error_detail(raw)})") from None


# --- Credenciales -------------------------------------------------------------

def secure_conf_dir():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":  # en Windows protege la ACL del perfil del usuario
        os.chmod(CONF_DIR, 0o700)


def save_credentials(tokens, previous_refresh=""):
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str) or not token:
        raise GiteaError("La respuesta de autenticación no trae access_token")
    expires_in = tokens.get("expires_in")
    content = json.dumps({
        "url": URL,
        "client_id": CLIENT_ID,
        "access_token": token,
        # Gitea puede invalidar el refresh al usarlo; siempre se guarda el último que devolvió
        "refresh_token": tokens.get("refresh_token") or previous_refresh,
        "expires_at": int(time.time()) + int(expires_in) if isinstance(expires_in, (int, float)) else None,
    })
    secure_conf_dir()
    # Archivo temporal 0600 en la misma carpeta y reemplazo atómico
    fd, tmp = tempfile.mkstemp(dir=CONF_DIR, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, CREDENTIALS_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_credentials():
    try:
        creds = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise GiteaError("No hay sesión; corre " + err.bold("gitea login")) from None
    except (ValueError, OSError) as e:
        raise GiteaError(f"No se pudo leer {CREDENTIALS_FILE}: {e}; corre gitea login") from None
    if not isinstance(creds, dict) or not creds.get("access_token"):
        raise GiteaError(f"{CREDENTIALS_FILE} está incompleto; corre gitea login")
    if creds.get("url") != URL:
        raise GiteaError(f"La sesión guardada es de {creds.get('url')}, no de {URL}; corre gitea login")
    return creds


def do_refresh(creds):
    """True si renovó; False si el servidor rechazó el refresh. Otros fallos se propagan."""
    if not creds.get("refresh_token"):
        return False
    try:
        _, raw, _ = token_request({"grant_type": "refresh_token", "refresh_token": creds["refresh_token"],
                                   "client_id": creds.get("client_id") or CLIENT_ID})
    except HttpError as e:
        if e.status in (400, 401, 403):
            return False
        raise
    save_credentials(parse_json(raw, "Renovar token"), creds["refresh_token"])
    return True


def fresh_credentials():
    creds = load_credentials()
    expires_at = creds.get("expires_at")
    if isinstance(expires_at, int) and expires_at - EXPIRY_MARGIN < time.time() and do_refresh(creds):
        creds = load_credentials()
    return creds


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
        ok = "code" in query
        page = ("Listo: ya puedes cerrar esta pestaña y volver a la terminal." if ok
                else "No se pudo iniciar sesión: " + query.get("error_description", query["error"])[0])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<!doctype html><meta charset=utf-8><title>gitea login</title><p>{page}</p>".encode())

    def log_message(self, *args):
        pass  # sin ruido en la terminal


def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def cmd_login(args):
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect_uri = f"http://127.0.0.1:{CALLBACK_PORT}"
    authorize = "/login/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    source = args.source if args.source is not None else AUTH_SOURCE
    # Con un proveedor, entra directo por él; Gitea guarda redirect_to y vuelve al authorize de OAuth2
    login_url = (f"{URL}/user/oauth2/{urllib.parse.quote(source)}?redirect_to={urllib.parse.quote(authorize)}"
                 if source else URL + authorize)

    try:
        server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
    except OSError as e:
        raise GiteaError(f"No se pudo escuchar en 127.0.0.1:{CALLBACK_PORT} ({e}); usa GITEA_CALLBACK_PORT") from None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    info(err.cyan("Abre este enlace para iniciar sesión:"))
    info(login_url)
    if not args.no_browser:
        webbrowser.open(login_url)
    try:
        deadline = time.time() + args.timeout
        while CallbackHandler.result is None and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
    result = CallbackHandler.result
    if result is None:
        raise GiteaError(f"No llegó la respuesta del navegador en {args.timeout} s")
    if result.get("state") != state:
        raise GiteaError("La respuesta del navegador no corresponde a este login (state distinto)")
    if "code" not in result:
        raise GiteaError("Gitea rechazó el login: " + result.get("error_description", result.get("error", "?")))

    try:
        _, raw, _ = token_request({"grant_type": "authorization_code", "code": result["code"],
                                   "redirect_uri": redirect_uri, "client_id": CLIENT_ID, "code_verifier": verifier})
    except HttpError as e:
        raise GiteaError(f"Error al obtener el token: {error_detail(e.body)}") from None
    save_credentials(parse_json(raw, "Iniciar sesión"))
    _, raw, _ = request("GET", "user", token=load_credentials()["access_token"])
    me = parse_json(raw, "Consultar usuario")
    info(err.green("✓ ") + f"Sesión iniciada en {URL} como {err.bold(me.get('login', '?'))}")


def cmd_refresh(args):
    if not do_refresh(load_credentials()):
        raise GiteaError("El servidor rechazó la renovación; corre " + err.bold("gitea login"))
    info(err.green("✓ ") + "Token renovado.")


def cmd_logout(args):
    CREDENTIALS_FILE.unlink(missing_ok=True)
    info(err.green("✓ ") + "Sesión cerrada.")


def cmd_token(args):
    print(fresh_credentials()["access_token"])


def call_with_refresh(method, path, body):
    creds = fresh_credentials()
    try:
        return request(method, path, body, creds["access_token"])
    except HttpError as e:
        if e.status != 401 or not do_refresh(creds):
            raise
        info(err.dim("El servidor respondió 401; token renovado y petición reintentada."))
        return request(method, path, body, load_credentials()["access_token"])


def colorize_json(value, indent=0):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}  {out.blue(json.dumps(k, ensure_ascii=False))}: {colorize_json(v, indent + 1)}"
                 for k, v in value.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        items = [f"{pad}  {colorize_json(v, indent + 1)}" for v in value]
        return "[\n" + ",\n".join(items) + f"\n{pad}]"
    text = json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return out.green(text)
    if value is None or isinstance(value, bool):
        return out.yellow(text)
    return out.cyan(text)


def print_response(raw, as_is, stream=None):
    stream = stream or sys.stdout
    if not raw:
        return
    try:
        data = json.loads(raw)
    except ValueError:
        as_is = True
    if as_is:
        print(raw, file=stream)
    elif out.enabled and stream is sys.stdout:
        print(colorize_json(data), file=stream)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2), file=stream)


def print_headers(status, headers):
    info(err.dim(f"HTTP {status}"))
    for name, value in headers.items():
        info(err.dim(f"{name}: {value}"))
    info("")


def read_body(data):
    if data is None:
        return None
    try:
        if data == "@-":
            body = sys.stdin.read()
        elif data.startswith("@"):
            body = Path(data[1:]).read_text(encoding="utf-8")
        else:
            body = data
    except OSError as e:
        raise GiteaError(f"No se pudo leer el cuerpo {data}: {e}") from None
    try:
        json.loads(body)
    except ValueError as e:
        raise GiteaError(f"El cuerpo -d no es JSON válido: {e}") from None
    return body


def cmd_api(args):
    body = read_body(args.data)
    try:
        status, raw, headers = call_with_refresh(args.method, args.path, body)
    except HttpError as e:
        if args.headers:
            print_headers(e.status, e.headers)
        info(err.red(f"✗ {args.method} {args.path} -> HTTP {e.status}"))
        if e.body.lstrip().startswith(("{", "[")):
            print_response(e.body, args.raw, sys.stderr)
        else:
            info(err.dim(error_detail(e.body)))
        return 1
    if args.headers:
        print_headers(status, headers)
    elif args.verbose:
        info(err.green(f"✓ {args.method} {args.path} -> HTTP {status}"))
    print_response(raw, args.raw)
    return 0


def cmd_me(args):
    _, raw, _ = call_with_refresh("GET", "user", None)
    me = parse_json(raw, "Consultar usuario")
    if not isinstance(me, dict):
        raise GiteaError("Respuesta inesperada de user")
    print(f"{out.bold(me.get('full_name') or me.get('login', ''))} ({out.cyan(me.get('login', '?'))}) en {URL}")
    expires_at = load_credentials().get("expires_at")
    if isinstance(expires_at, int):
        print(out.dim(f"Token vigente hasta {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires_at))}"))
    print(out.dim(f"Credenciales: {CREDENTIALS_FILE}"))


def build_parser():
    epilog = f"""\
ejemplos:
  gitea login --url https://gitea.example.com       # la primera vez; abre el navegador
  gitea login --source <proveedor>                  # entra directo por un proveedor externo de Gitea (OIDC)
  gitea me
  gitea GET "repos/<owner>/<repo>/pulls?state=open"
  gitea POST repos/<owner>/<repo>/pulls -d @pr.json
  gitea PATCH repos/<owner>/<repo>/pulls/<n> -d '{{"title": "Nuevo título"}}'
  export GITEA_TOKEN="$(gitea token)"               # para otros scripts

variables de entorno:
  GITEA_URL            servidor, solo https; si falta, el de la última sesión (actual: {ENV_URL or "sin definir"})
  GITEA_AUTH_SOURCE    proveedor de login de Gitea; vacío = página de login de Gitea (actual: {AUTH_SOURCE!r})
  GITEA_CLIENT_ID      app OAuth2 pública de Gitea (actual: {CLIENT_ID})
  GITEA_CALLBACK_PORT  puerto local para la respuesta del navegador (actual: {CALLBACK_PORT})
  GITEA_CONF_DIR       carpeta de credenciales (actual: {CONF_DIR})
  GITEA_CACERT         CA adicional (actual: {CACERT})
  NO_COLOR             desactiva los colores
"""
    parser = argparse.ArgumentParser(
        prog="gitea", description="Cliente de la API de Gitea.", epilog=epilog,
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

    p = sub.add_parser("api", help="llama a la API: gitea [api] MÉTODO ruta [-d JSON]",
                       description="Llama a /api/v1/<ruta> del servidor configurado. "
                                   "Renueva el token si venció o si recibe 401, y reintenta una vez.")
    p.add_argument("method", metavar="MÉTODO", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="ruta", help='relativa a /api/v1/, p. ej. "repos/<owner>/<repo>/pulls"')
    p.add_argument("-d", "--data", metavar="JSON", help="cuerpo JSON, @archivo o @- para stdin")
    p.add_argument("-r", "--raw", action="store_true", help="imprime el cuerpo tal cual llega")
    p.add_argument("-i", "--headers", action="store_true", help="muestra estado y cabeceras (en stderr)")
    p.add_argument("-v", "--verbose", action="store_true", help="muestra el código HTTP")
    p.set_defaults(func=cmd_api)
    return parser


def main(argv):
    setup_streams()
    # Atajo: "gitea GET ruta" equivale a "gitea api GET ruta"
    if argv and argv[0].upper() in METHODS:
        argv = ["api"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    global URL
    try:
        URL = resolve_url(getattr(args, "url", None))
        return args.func(args) or 0
    except GiteaError as e:
        info(err.red("✗ ") + str(e))
        return 1
    except BrokenPipeError:
        # p. ej. "gitea GET ... | head": no es un error
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        sys.exit(130)
