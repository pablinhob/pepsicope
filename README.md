# Boldest Periscope

Vigilancia incremental del workspace de Bitbucket. Cada ejecución genera un informe Markdown
en `reports/` con lo que ha cambiado desde la ejecución anterior. Combina:

- **Detección determinista**: API de Bitbucket y clones git locales. Saca autores, fechas,
  dependencias, archivos y diffs, siempre a partir de datos reales.
- **Interpretación con Claude**: para qué sirve un repo, por qué se añadió una biblioteca,
  funcionalidades ocultas, cambios en la forma de trabajar y resumen final.

El primer informe es la **línea base**: lista todo lo que existe. Los siguientes solo muestran
las diferencias con el anterior.

Junto a cada informe se genera su versión HTML con el mismo nombre y `.html` al final
(`…_linea-base.md.html`). Es un único fichero que se abre en cualquier navegador sin conexión:
no carga nada de fuera. Permite filtrar por repositorio, proyecto y lenguaje, buscar texto y
ver solo los avisos ⚠️, y guarda los filtros en la URL (`#repo=bwidget`). Para generar el HTML
de informes que ya existen: `./workspace-periscope.sh html` (todos) o `./workspace-periscope.sh html reports/x.md`.

## Requisitos

Funciona en **macOS** y **Linux** (probado en macOS y en Debian). Hace falta:

- `bash`, `git`, `curl` y `ssh` (vienen de serie en macOS; en Debian/Ubuntu:
  `sudo apt install git curl openssh-client`).
- Una clave SSH con acceso a Bitbucket (`ssh -T git@bitbucket.org` debe decir "authenticated").
- [Claude Code](https://claude.com/claude-code) instalado y con la sesión iniciada, para la parte de IA.

Python y uv no hace falta instalarlos: `./workspace-periscope.sh setup` instala uv (con Homebrew si está
disponible y, si no, con el instalador oficial) y uv descarga Python 3.15.

## Puesta en marcha

`./workspace-periscope` y `./workspace-periscope.sh` son lo mismo (el primero es un enlace al segundo).

```bash
./workspace-periscope.sh setup      # instala uv + Python 3.15, crea .venv, instala dependencias y crea .env
# edita .env: workspace, credenciales de Bitbucket y funcionalidades
./workspace-periscope.sh doctor     # comprueba .env, git, SSH, Claude Code y la API de Bitbucket
./workspace-periscope.sh doctor --ai   # además hace una llamada de prueba a Claude
./workspace-periscope.sh run        # genera el informe
```

Para una primera prueba rápida:

```bash
./workspace-periscope.sh run --max-repos 3 --no-save      # 3 repos, sin guardar estado
./workspace-periscope.sh run --repo '^api-' --no-ai       # solo repos que empiezan por api-, sin IA
```

### Acceso a Bitbucket

- **Código (git) → SSH**, con tu clave habitual. Compruébalo con `ssh -T git@bitbucket.org`.
  Si la clave tiene passphrase, tiene que estar cargada en el agente (`ssh-add`), porque
  Periscope no puede preguntártela.
- **Lista de repos, proyectos, miembros y metadatos → API REST con email + API token.** Esto no se
  puede obtener por SSH. Crea el token en
  <https://id.atlassian.com/manage-profile/security/api-tokens> → *Create API token with scopes* → Bitbucket,
  con `read:workspace:bitbucket`, `read:project:bitbucket`, `read:repository:bitbucket` y `read:user:bitbucket`,
  y rellena `BITBUCKET_EMAIL` y `BITBUCKET_API_TOKEN`.

#### Scopes del token

Esos cuatro scopes bastan para lo que hace Periscope hoy. El token del equipo tiene además
otros scopes de solo lectura. Aquí se listan todos, con lo que se usa y lo que podría
aprovecharse en funcionalidades futuras, cada una con su `FEATURE_*`.

| Scope | Uso actual | Posible uso futuro |
|---|---|---|
| `read:workspace:bitbucket` | Workspace y miembros | — |
| `read:project:bitbucket` | Proyectos | — |
| `read:repository:bitbucket` | Repos y metadatos | Ramas y restricciones de rama |
| `read:user:bitbucket` | Usuario actual (`doctor`) | — |
| `read:account` | — | Perfil de la cuenta Atlassian (nombre y email de autores) |
| `read:pullrequest:bitbucket` | — | PRs abiertas y fusionadas, revisores, tiempo hasta merge (prácticas de revisión) |
| `read:pipeline:bitbucket` | — | Ejecuciones de pipelines, tasa de fallo, repos que adoptan CI, deployments |
| `read:runner:bitbucket` | — | Runners self-hosted nuevos (infraestructura propia) |
| `read:permission:bitbucket` | — | Cambios de permisos: quién gana acceso o pasa a admin (seguridad) |
| `read:webhook:bitbucket` | — | Webhooks nuevos hacia servicios externos (integraciones ocultas) |
| `read:issue:bitbucket` | — | Uso del issue tracker de cada repo |
| `read:wiki:bitbucket` | — | Wikis nuevas o con cambios (documentación) |
| `read:snippet:bitbucket` | — | Snippets del workspace: código fuera de los repos y posibles secretos expuestos |
| `read:package:bitbucket` | — | Paquetes publicados en el registro de Bitbucket |
| `read:agent-session:bitbucket` | — | Sesiones de agentes de IA en Bitbucket (adopción de IA) |
| `read:user-orgs` | — | Organización Atlassian a la que pertenece el usuario |
| `read:user-org-units` | — | Unidades organizativas (equipos) de la organización |
| `read:user-org-unit-apps` | — | Apps instaladas por unidad organizativa |

Todo es de solo lectura. Aunque el token pudiera escribir, Periscope no escribe nunca en
Bitbucket.

### Claude

No hace falta ninguna clave ni créditos de API. Periscope llama al Claude Code instalado
(`claude -p`) con tu sesión, igual que si le hicieras las preguntas a mano, y el consumo
cuenta contra tu plan de Claude. Si la sesión caduca, abre `claude` y ejecuta `/login`.

Con `AI_BACKEND=none` (o `run --no-ai`) solo se usa la detección determinista.

Las respuestas de la IA se guardan en caché (`data/ai_cache/`). Si repites una ejecución sobre
los mismos datos, no se vuelve a consumir.

## Funcionalidades (`.env`, 1/0)

| Variable | Qué detecta |
|---|---|
| `FEATURE_NEW_REPOS` | Repos nuevos: creador (autor del primer commit), fecha, lenguaje, stack, dependencias y, con IA, para qué sirven |
| `FEATURE_DELETED_REPOS` | Repos eliminados, último commit y motivo probable (IA) |
| `FEATURE_REPO_METADATA` | Renombrados, cambio de visibilidad (⚠️ si pasa a público), de proyecto o de rama principal |
| `FEATURE_WORKSPACE` | Proyectos y miembros nuevos o eliminados |
| `FEATURE_BRANCHES` | Ramas nuevas con sus commits sin fusionar (pista de trabajo oculto) |
| `FEATURE_LIBRARIES` | Bibliotecas añadidas o eliminadas, saltos de versión mayor y **qué commit/autor las metió** |
| `FEATURE_FRAMEWORKS` | Frameworks y sub-frameworks nuevos o abandonados |
| `FEATURE_EXTERNAL_SERVICES` | SDKs de servicios, hosts externos en el código e imágenes de docker-compose |
| `FEATURE_PRACTICES` | Primeros tests, docblocks, linters y archivos de IA (`CLAUDE.md`, `.cursor/`, `AGENTS.md`…) |
| `FEATURE_INFRA` | Docker, devcontainers, Vagrant, Kubernetes, Terraform… (destaca cuando aparece por primera vez) |
| `FEATURE_CONFIG_PIPELINES` | Cambios en `bitbucket-pipelines.yml`, Dockerfiles, `.env.example`, configuración… |
| `FEATURE_AI_REPO_PURPOSE` | IA: interpretación de cada repo nuevo |
| `FEATURE_AI_CHANGE_ANALYSIS` | IA: lee commits y diff de cada repo con cambios (intenciones, servicios, funcionalidades ocultas, prácticas) |
| `FEATURE_AI_SUMMARY` | IA: resumen final |
| `FEATURE_AUDIT_LOG` | Quién creó o borró repos, según el audit log de Atlassian (requiere Atlassian Guard y clave de admin) |

Para desactivar algo puntualmente sin tocar `.env`: `FEATURE_AI_SUMMARY=0 ./workspace-periscope.sh run`.

Para controlar el coste del primer informe tienes `AI_MAX_CALLS`, `MAX_REPOS`, `REPO_INCLUDE` y `REPO_EXCLUDE`.

### Limitaciones a tener en cuenta

- **Quién borró un repo**: la API de Bitbucket no lo expone. Solo se sabe con `FEATURE_AUDIT_LOG`.
  Sin él, el informe da el intervalo de fechas en el que desapareció.
- **Quién creó un repo**: se usa el autor del primer commit, que casi siempre coincide.
  El audit log da el dato exacto.
- El análisis de código se hace sobre la **rama principal**. Del resto de ramas se reportan las
  nuevas, con sus commits y archivos.

## Gestión del entorno

```bash
./workspace-periscope.sh help
```

| Comando | Para qué |
|---|---|
| `setup` | Instala todo desde cero |
| `./dev-start-env.sh` / `./dev-stop-env.sh` | Arranca / para el entorno (también `./workspace-periscope.sh start` / `stop`) |
| `status` | Estado del entorno, snapshots, informes y cachés |
| `doctor [--ai]` | Diagnóstico de configuración y conexión |
| `reset` | Reinstala `.venv` desde cero (sin tocar datos) |
| `destroy` | Borra `.venv` |
| `python-upgrade [3.x]` | Cambia a otra versión de Python (por defecto la última) |
| `install` | Instala lo fijado en `uv.lock` |
| `add <pkg>` / `add --dev <pkg>` / `remove <pkg>` | Gestiona bibliotecas |
| `upgrade [pkg]` / `outdated` / `list` / `tree` | Actualiza o inspecciona bibliotecas |
| `export` | Genera `requirements.txt` |
| `run [--no-ai] [--no-save] [--repo RE] [--max-repos N] [-v]` | Genera un informe |
| `html [informe.md…]` | Genera el HTML de informes existentes (por defecto, todos los de `reports/`) |
| `features` | Muestra qué funcionalidades están activas |
| `reports` / `last` | Lista los informes o abre el último en VS Code |
| `reset-state snapshots\|ai-cache\|repos\|all` | Borra estado. Si borras los snapshots, el siguiente informe vuelve a ser línea base |
| `test` | Tests (pytest) |

Estilo de código (PEP 8), con scripts propios:

| Script | Para qué |
|---|---|
| `./dev-style-check.sh` | Comprueba sin tocar nada: Black (formato, con diff) y Ruff (PEP 8, nombres, imports, bugs) |
| `./dev-style-fix.sh` | Corrige: Black reformatea, Ruff arregla lo automático y se lista lo que queda a mano |

## Estructura

```
workspace-periscope.sh  gestor del entorno y la app
dev-start-env.sh        arranca el entorno
dev-stop-env.sh         para el entorno
dev-style-check.sh      comprueba el estilo (Black + Ruff), sin tocar nada
dev-style-fix.sh        corrige el estilo
.env / .env.example     configuración y secretos (.env no se versiona)
src/periscope/
  cli.py                comandos
  config.py             carga de .env y flags
  bitbucket.py          API de Bitbucket y audit log de Atlassian
  gitrepo.py            clones parciales por SSH, incrementales, y consultas git
  scanners.py           dependencias, frameworks, servicios, prácticas, infra, config
  snapshot.py           foto del estado del workspace
  analysis.py           comparación, atribución de autores, prompts a Claude
  ai.py                 llamadas a Claude Code, con caché
  report.py             render del Markdown
  html_report.py        conversión del informe a HTML autocontenido con filtros
data/                   estado local (no versionado)
  repos/                clones parciales (el primer run descarga, después solo lo nuevo)
  snapshots/            un JSON por ejecución (base de comparación)
  ai_cache/             respuestas de la IA
reports/                AAAA-MM-DD_HH-MM-SS_<workspace>_<linea-base|cambios>.md (+ .md.html)
```

## Ejecución periódica

Con cron, por ejemplo de lunes a viernes a las 7:00:

```cron
0 7 * * 1-5 cd /ruta/a/boldest-periscope && ./workspace-periscope.sh run >> data/cron.log 2>&1
```

El usuario que ejecuta el cron debe tener iniciada la sesión de Claude Code y acceso SSH sin passphrase interactiva.
