# cli-tools

Herramientas de línea de comandos para trabajar con las APIs de Taiga y Gitea. Solo usan la librería estándar de Python 3 (3.9 o superior), sin dependencias.

| Comando | Qué hace |
|---|---|
| `taiga` | Cliente de la API de Taiga: login con usuario y contraseña, renovación automática del token y llamadas `GET/POST/PUT/PATCH/DELETE` a `/api/v1/`. |
| `gitea` | Cliente de la API de Gitea: login OAuth2 con PKCE en el navegador (también por un proveedor externo de Gitea, como un OIDC), renovación automática del token y llamadas a `/api/v1/`. |

## Instalación

```bash
ln -s "$PWD/scripts/taiga.py" ~/.local/bin/taiga
ln -s "$PWD/scripts/gitea.py" ~/.local/bin/gitea
```

## Uso

```bash
taiga login --url https://taiga.example.com     # la primera vez; el servidor queda guardado
taiga me
taiga GET "userstories?project=<id>"

gitea login --url https://gitea.example.com     # abre el navegador
gitea login --source <proveedor>                # entra directo por un proveedor externo de Gitea
gitea GET "repos/<owner>/<repo>/pulls?state=open"
gitea POST repos/<owner>/<repo>/pulls -d @pr.json

export TAIGA_TOKEN="$(taiga token)"             # para otros scripts
```

`taiga --help` y `gitea --help` listan todos los comandos y variables de entorno.

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
