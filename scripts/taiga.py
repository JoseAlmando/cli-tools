#!/usr/bin/env python3
"""Cliente de línea de comandos para la API de Taiga."""
import argparse
import getpass
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONF_DIR = Path(os.environ.get("TAIGA_CONF_DIR") or Path.home() / ".config" / "taiga")
ENV_URL = os.environ.get("TAIGA_URL", "").rstrip("/")
URL = ""  # main() lo resuelve: --url, TAIGA_URL o el servidor de la última sesión
# CA adicional opcional (p. ej. una CA interna); si no existe se usa el almacén del sistema
CACERT = Path(os.environ.get("TAIGA_CACERT") or CONF_DIR / "ca.pem")
CREDENTIALS_FILE = CONF_DIR / "credentials.json"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class TaigaError(Exception):
    """Error esperable; main() lo muestra sin traceback."""


class HttpError(TaigaError):
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
        raise TaigaError(f"TAIGA_URL debe ser https://servidor (es {url!r})")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise TaigaError(f"TAIGA_URL no puede llevar usuario, query ni fragmento ({url!r})")
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
        raise TaigaError("No hay servidor configurado; usa taiga login --url https://servidor o define TAIGA_URL")
    return validate_base_url(saved)


def api_url(base, path):
    """Solo rutas relativas a la API: el token nunca sale del servidor configurado."""
    if "://" in path or path.startswith("//") or "\\" in path:
        raise TaigaError(f"Solo se aceptan rutas relativas a {base}/api/v1/ (recibí {path!r})")
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
            raise TaigaError(f"El certificado {CACERT} no es válido: {e}") from None
    return ctx


def request(method, path, body=None, token=None):
    """Devuelve (status, cuerpo, cabeceras). Lanza HttpError o TaigaError; nunca termina el programa."""
    url = api_url(validate_base_url(URL), path)
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), dict(e.headers)) from None
    except urllib.error.URLError as e:
        raise TaigaError(f"Sin conexión con {URL}: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise TaigaError(f"Fallo de red con {URL}: {e}") from None


def error_detail(body):
    """Siempre devuelve texto, sea cual sea la forma de la respuesta."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return " ".join(str(body).split())[:200]
    if isinstance(data, dict):
        data = data.get("_error_message") or data.get("detail") or data
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError:
        raise TaigaError(f"{what}: el servidor no devolvió JSON ({error_detail(raw)})") from None


# --- Credenciales -------------------------------------------------------------

def secure_conf_dir():
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":  # en Windows protege la ACL del perfil del usuario
        os.chmod(CONF_DIR, 0o700)


def save_credentials(url, auth, previous_refresh=""):
    token = auth.get("auth_token") if isinstance(auth, dict) else None
    if not isinstance(token, str) or not token:
        raise TaigaError("La respuesta de autenticación no trae auth_token")
    refresh = auth.get("refresh") or previous_refresh
    secure_conf_dir()
    content = json.dumps({"url": url, "auth_token": token, "refresh": refresh})
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
        raise TaigaError("No hay sesión; corre " + err.bold("taiga login")) from None
    except (ValueError, OSError) as e:
        raise TaigaError(f"No se pudo leer {CREDENTIALS_FILE}: {e}; corre taiga login") from None
    if not isinstance(creds, dict) or not creds.get("auth_token"):
        raise TaigaError(f"{CREDENTIALS_FILE} está incompleto; corre taiga login")
    if creds.get("url") != URL:
        raise TaigaError(f"La sesión guardada es de {creds.get('url')}, no de {URL}; corre taiga login")
    return creds


def do_refresh(creds):
    """True si renovó; False si el servidor rechazó el refresh. Otros fallos se propagan."""
    if not creds.get("refresh"):
        return False
    try:
        _, raw, _ = request("POST", "auth/refresh", json.dumps({"refresh": creds["refresh"]}))
    except HttpError as e:
        if e.status in (400, 401, 403):
            return False
        raise
    save_credentials(URL, parse_json(raw, "Renovar token"), creds["refresh"])
    return True


# --- Comandos -----------------------------------------------------------------

def cmd_login(args):
    user = args.user or input(err.cyan("Usuario o correo: "))
    password = getpass.getpass(err.cyan("Contraseña: "))
    body = json.dumps({"type": "normal", "username": user, "password": password})
    try:
        _, raw, _ = request("POST", "auth", body)
    except HttpError as e:
        raise TaigaError(f"Error al iniciar sesión: {error_detail(e.body)}") from None
    data = parse_json(raw, "Iniciar sesión")
    save_credentials(URL, data)
    info(err.green("✓ ") + f"Sesión iniciada en {URL} como {err.bold(data.get('username', user))}")


def cmd_refresh(args):
    if not do_refresh(load_credentials()):
        raise TaigaError("El servidor rechazó la renovación; corre " + err.bold("taiga login"))
    info(err.green("✓ ") + "Token renovado.")


def cmd_logout(args):
    CREDENTIALS_FILE.unlink(missing_ok=True)
    info(err.green("✓ ") + "Sesión cerrada.")


def cmd_token(args):
    print(load_credentials()["auth_token"])


def call_with_refresh(method, path, body):
    creds = load_credentials()
    try:
        return request(method, path, body, creds["auth_token"])
    except HttpError as e:
        if e.status != 401 or not do_refresh(creds):
            raise
        info(err.dim("El servidor respondió 401; token renovado y petición reintentada."))
        return request(method, path, body, load_credentials()["auth_token"])


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
        raise TaigaError(f"No se pudo leer el cuerpo {data}: {e}") from None
    try:
        json.loads(body)
    except ValueError as e:
        raise TaigaError(f"El cuerpo -d no es JSON válido: {e}") from None
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
    _, raw, _ = call_with_refresh("GET", "users/me", None)
    me = parse_json(raw, "Consultar usuario")
    if not isinstance(me, dict):
        raise TaigaError("Respuesta inesperada de users/me")
    print(f"{out.bold(me.get('full_name_display', ''))} ({out.cyan(me.get('username', '?'))}) en {URL}")
    print(out.dim(f"Credenciales: {CREDENTIALS_FILE}"))


def build_parser():
    epilog = f"""\
ejemplos:
  taiga login --url https://taiga.example.com      # la primera vez; después basta con taiga login
  taiga me
  taiga GET "userstories?project=<id>"
  taiga GET "userstories?project=<id>&page=2" -i   # ver cabeceras x-pagination-*
  taiga PATCH userstories/123 -d '{{"version": 4, "subject": "Nuevo título"}}'
  taiga POST tasks -d @tarea.json
  export TAIGA_TOKEN="$(taiga token)"               # para otros scripts

variables de entorno:
  TAIGA_URL         servidor, solo https; si falta, el de la última sesión (actual: {ENV_URL or "sin definir"})
  TAIGA_CONF_DIR    carpeta de credenciales (actual: {CONF_DIR})
  TAIGA_CACERT      CA adicional (actual: {CACERT})
  NO_COLOR          desactiva los colores
"""
    parser = argparse.ArgumentParser(
        prog="taiga", description="Cliente de la API de Taiga.", epilog=epilog,
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

    p = sub.add_parser("api", help="llama a la API: taiga [api] MÉTODO ruta [-d JSON]",
                       description="Llama a /api/v1/<ruta> del servidor configurado. "
                                   "Si recibe 401, renueva el token y reintenta una vez.")
    p.add_argument("method", metavar="MÉTODO", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="ruta", help='relativa a /api/v1/, p. ej. "userstories?project=<id>"')
    p.add_argument("-d", "--data", metavar="JSON", help="cuerpo JSON, @archivo o @- para stdin")
    p.add_argument("-r", "--raw", action="store_true", help="imprime el cuerpo tal cual llega")
    p.add_argument("-i", "--headers", action="store_true", help="muestra estado y cabeceras (en stderr)")
    p.add_argument("-v", "--verbose", action="store_true", help="muestra el código HTTP")
    p.set_defaults(func=cmd_api)
    return parser


def main(argv):
    setup_streams()
    # Atajo: "taiga GET ruta" equivale a "taiga api GET ruta"
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
    except TaigaError as e:
        info(err.red("✗ ") + str(e))
        return 1
    except BrokenPipeError:
        # p. ej. "taiga GET ... | head": no es un error
        sys.stderr.close()
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(file=sys.stderr)
        sys.exit(130)
