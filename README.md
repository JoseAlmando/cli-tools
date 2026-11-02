# cli-tools

Herramientas de línea de comandos para trabajar con las APIs de Taiga y Gitea, reunidas bajo un solo comando, `clt`. Solo usan la librería estándar de Python, sin dependencias.

| Comando | Qué hace |
|---|---|
| `clt` | Agrupa las herramientas: `clt taiga …`, `clt gitea …`. |
| `taiga` | Cliente de la API de Taiga: login con usuario y contraseña, renovación automática del token y llamadas `GET/POST/PUT/PATCH/DELETE` a `/api/v1/`. |
| `gitea` | Cliente de la API de Gitea: login OAuth2 con PKCE en el navegador (también por un proveedor externo de Gitea, como un OIDC), renovación automática del token y llamadas a `/api/v1/`. |

## Instalación

Necesita Python 3.9 o superior. Instala tres comandos: `clt`, que agrupa las herramientas, y `taiga` y `gitea` sueltos. En Windows se generan como `.exe`.

```bash
uv tool install -e .        # o: pipx install -e .
```

Con `-e`, los cambios en el repo se ven al instante, sin reinstalar. Para desinstalar: `uv tool uninstall cli-tools`.

Sin instalar, desde el repo:

```bash
PYTHONPATH=src python -m cli_tools taiga me
```

## Uso

```bash
clt taiga me                                    # igual que: taiga me

taiga login --url https://taiga.example.com     # la primera vez; el servidor queda guardado
taiga me
taiga GET "userstories?project=<id>"

gitea login --url https://gitea.example.com     # abre el navegador
gitea login --source <proveedor>                # entra directo por un proveedor externo de Gitea
gitea GET "repos/<owner>/<repo>/pulls?state=open"
gitea POST repos/<owner>/<repo>/pulls -d @pr.json

export TAIGA_TOKEN="$(taiga token)"             # para otros scripts
```

`clt --help`, `taiga --help` y `gitea --help` listan los comandos y las variables de entorno.

## Configuración

Ningún servidor viene configurado por defecto. Cada herramienta lo toma, en este orden:
1. la opción `--url` de `login`;
2. la variable de entorno `TAIGA_URL` o `GITEA_URL`;
3. el servidor de la última sesión.

| Variable | Para qué sirve |
|---|---|
| `TAIGA_URL`, `GITEA_URL` | Servidor, solo `https://`. |
| `TAIGA_CONF_DIR`, `GITEA_CONF_DIR` | Carpeta de credenciales; por defecto `~/.config/taiga` y `~/.config/gitea`. |
| `TAIGA_CACERT`, `GITEA_CACERT` | CA adicional opcional, por ejemplo una CA interna; por defecto `ca.pem` en la carpeta de credenciales. |
| `GITEA_AUTH_SOURCE` | Proveedor externo de Gitea por el que entrar directo; vacío significa la página de login de Gitea. |
| `GITEA_CLIENT_ID` | App OAuth2 pública de Gitea; por defecto, la que Gitea trae integrada para `tea`. |
| `GITEA_CALLBACK_PORT` | Puerto local para la respuesta del navegador; por defecto `38291`. |
| `NO_COLOR` | Desactiva los colores. |

## Seguridad

- **Credenciales:** se guardan en `credentials.json`, con permisos `0600`, dentro de una carpeta `0700`, y se escriben de forma atómica. Nunca van al repo.
- **Token:** solo se envía a rutas relativas de la API del servidor configurado, y no se siguen redirecciones (urllib reenviaría la cabecera `Authorization`).

## Desarrollo

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .
```

| Ruta | Contenido |
|---|---|
| `src/cli_tools/core.py` | Lo común: HTTP, credenciales, salida y el subcomando `api`. |
| `src/cli_tools/taiga.py`, `gitea.py` | Una herramienta por archivo: su login, su renovación de token y sus comandos. |
| `src/cli_tools/clt.py` | El comando `clt`. Una herramienta nueva se agrega a `TOOLS`. |
| `tests/` | Tests con `unittest`, sin red ni servidores. |

## Licencia

[MIT](LICENSE) © 2026 José A. Dominique.
