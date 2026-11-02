"""Piezas comunes a todas las herramientas: HTTP, credenciales, salida y el subcomando api."""
import json
import os
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


class ToolError(Exception):
    """Error esperable; run() lo muestra sin traceback."""


class HttpError(ToolError):
    def __init__(self, status, body, headers=None):
        super().__init__(f"HTTP {status}: {error_detail(body)}")
        self.status = status
        self.body = body
        self.headers = headers or {}


# --- Salida -------------------------------------------------------------------

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


# --- Servidor y HTTP ------------------------------------------------------------

def validate_base_url(url, var):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ToolError(f"{var} debe ser https://servidor (es {url!r})")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ToolError(f"{var} no puede llevar usuario, query ni fragmento ({url!r})")
    return url.rstrip("/")


def resolve_url(cli_url, env_url, credentials_file, prog, var):
    """Servidor: --url de login, si no la variable de entorno, si no el de la última sesión."""
    if cli_url:
        return validate_base_url(cli_url, var)
    if env_url:
        return validate_base_url(env_url, var)
    saved = read_json_file(credentials_file)
    if not isinstance(saved, dict) or not saved.get("url"):
        raise ToolError(f"No hay servidor configurado; usa {prog} login --url https://servidor o define {var}")
    return validate_base_url(saved["url"], var)


def api_url(base, path):
    """Solo rutas relativas a la API: el token nunca sale del servidor configurado."""
    if "://" in path or path.startswith("//") or "\\" in path:
        raise ToolError(f"Solo se aceptan rutas relativas a {base}/api/v1/ (recibí {path!r})")
    return f"{base}/api/v1/{path.lstrip('/')}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib reenvía Authorization al seguir redirecciones; no las seguimos
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ssl_context(cacert):
    ctx = ssl.create_default_context()
    if cacert and Path(cacert).is_file():
        try:
            ctx.load_verify_locations(str(cacert))
        except ssl.SSLError as e:
            raise ToolError(f"El certificado {cacert} no es válido: {e}") from None
    return ctx


def send(req, cacert):
    """Devuelve (status, cuerpo, cabeceras). Lanza HttpError o ToolError; nunca termina el programa."""
    server = urllib.parse.urlsplit(req.full_url).hostname
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context(cacert)), NoRedirect)
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace"), dict(e.headers)) from None
    except urllib.error.URLError as e:
        raise ToolError(f"Sin conexión con {server}: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise ToolError(f"Fallo de red con {server}: {e}") from None


def json_request(method, url, body, token, cacert):
    req = urllib.request.Request(url, data=body.encode("utf-8") if isinstance(body, str) else body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return send(req, cacert)


def error_detail(body):
    """Siempre devuelve texto, sea cual sea la forma de la respuesta."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return " ".join(str(body).split())[:200]
    if isinstance(data, dict):
        for key in ("_error_message", "detail", "message", "error_description", "error"):
            if data.get(key):
                data = data[key]
                break
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)[:200]


def parse_json(raw, what):
    try:
        return json.loads(raw)
    except ValueError:
        raise ToolError(f"{what}: el servidor no devolvió JSON ({error_detail(raw)})") from None


# --- Credenciales -------------------------------------------------------------

def read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, OSError):
        return None


def write_private_json(path, data):
    """Escribe con permisos 0600 en una carpeta 0700, de forma atómica."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":  # en Windows protege la ACL del perfil del usuario
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- Subcomando api ---------------------------------------------------------------

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
        raise ToolError(f"No se pudo leer el cuerpo {data}: {e}") from None
    try:
        json.loads(body)
    except ValueError as e:
        raise ToolError(f"El cuerpo -d no es JSON válido: {e}") from None
    return body


def add_api_parser(sub, prog, example_path, func):
    p = sub.add_parser("api", help=f"llama a la API: {prog} [api] MÉTODO ruta [-d JSON]",
                       description="Llama a /api/v1/<ruta> del servidor configurado. "
                                   "Si el token venció, lo renueva y reintenta una vez.")
    p.add_argument("method", metavar="MÉTODO", type=str.upper, choices=METHODS, help=", ".join(METHODS))
    p.add_argument("path", metavar="ruta", help=f"relativa a /api/v1/, p. ej. {example_path!r}")
    p.add_argument("-d", "--data", metavar="JSON", help="cuerpo JSON, @archivo o @- para stdin")
    p.add_argument("-r", "--raw", action="store_true", help="imprime el cuerpo tal cual llega")
    p.add_argument("-i", "--headers", action="store_true", help="muestra estado y cabeceras (en stderr)")
    p.add_argument("-v", "--verbose", action="store_true", help="muestra el código HTTP")
    p.set_defaults(func=func)


def run_api(args, call):
    """call(método, ruta, cuerpo) -> (status, cuerpo, cabeceras), con la renovación del token de cada herramienta."""
    body = read_body(args.data)
    try:
        status, raw, headers = call(args.method, args.path, body)
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


# --- Arranque ---------------------------------------------------------------------

def with_api_shortcut(argv):
    """Atajo: "<herramienta> GET ruta" equivale a "<herramienta> api GET ruta"."""
    return ["api"] + argv if argv and argv[0].upper() in METHODS else argv


def run(main, argv=None):
    """Ejecuta main(argv) y traduce los errores esperables a códigos de salida."""
    setup_streams()
    try:
        return main(sys.argv[1:] if argv is None else argv) or 0
    except ToolError as e:
        info(err.red("✗ ") + str(e))
        return 1
    except BrokenPipeError:
        # p. ej. "taiga GET ... | head": no es un error
        sys.stderr.close()
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
