"""Detección determinista: dependencias, frameworks, servicios externos, prácticas, infra y config.

Todo trabaja sobre el árbol de archivos en HEAD y el working tree del clon local.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from pathlib import PurePosixPath

import yaml

from periscope.gitrepo import Repo

IGNORED_DIRS = (
    "node_modules/",
    "vendor/",
    "bower_components/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    "site-packages/",
    "Pods/",
    ".yarn/",
    "third_party/",
    "public/build/",
)

MAX_MANIFESTS = 60


def _ignored(path: str) -> bool:
    p = "/" + path
    return any(f"/{d}" in p for d in IGNORED_DIRS)


# =========================================================================
#  Dependencias
# =========================================================================
def _pkg_json(text: str) -> dict[str, str]:
    data = json.loads(text)
    out = {}
    for sec, suffix in (
        ("dependencies", ""),
        ("devDependencies", " (dev)"),
        ("peerDependencies", " (peer)"),
    ):
        for name, ver in (data.get(sec) or {}).items():
            out[name] = f"{ver}{suffix}"
    return out


def _composer(text: str) -> dict[str, str]:
    data = json.loads(text)
    out = {}
    for sec, suffix in (("require", ""), ("require-dev", " (dev)")):
        for name, ver in (data.get(sec) or {}).items():
            if name == "php" or name.startswith(("ext-", "lib-")):
                continue
            out[name] = f"{ver}{suffix}"
    return out


_REQ_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")


def _req_line(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http://", "https://")):
        return None
    m = _REQ_RE.match(line)
    if not m:
        return None
    return m.group(1).lower().replace("_", "-"), (m.group(3).split(";")[0].strip() or "*")


def _requirements(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        parsed = _req_line(line)
        if parsed:
            out[parsed[0]] = parsed[1]
    return out


def _pyproject(text: str) -> dict[str, str]:
    data = tomllib.loads(text)
    out: dict[str, str] = {}
    proj = data.get("project") or {}
    specs = list(proj.get("dependencies") or [])
    for group in (proj.get("optional-dependencies") or {}).values():
        specs += group
    for group in (data.get("dependency-groups") or {}).values():
        specs += [g for g in group if isinstance(g, str)]
    for spec in specs:
        parsed = _req_line(spec)
        if parsed:
            out[parsed[0]] = parsed[1]
    poetry = (data.get("tool") or {}).get("poetry") or {}
    sections = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    for grp in (poetry.get("group") or {}).values():
        sections.append(grp.get("dependencies") or {})
    for sec in sections:
        for name, ver in sec.items():
            if name.lower() != "python":
                out[name.lower()] = ver if isinstance(ver, str) else json.dumps(ver)
    return out


def _pipfile(text: str) -> dict[str, str]:
    data = tomllib.loads(text)
    out = {}
    for sec in ("packages", "dev-packages"):
        for name, ver in (data.get(sec) or {}).items():
            out[name.lower()] = ver if isinstance(ver, str) else json.dumps(ver)
    return out


def _gomod(text: str) -> dict[str, str]:
    out = {}
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require ") :]
        elif not in_block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def _gemfile(text: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(
        r"""^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""", text, re.M
    ):
        out[m.group(1)] = m.group(2) or "*"
    return out


def _cargo(text: str) -> dict[str, str]:
    data = tomllib.loads(text)
    out = {}
    for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name, ver in (data.get(sec) or {}).items():
            out[name] = ver if isinstance(ver, str) else str((ver or {}).get("version", "*"))
    return out


def _pom(text: str) -> dict[str, str]:
    text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
    root = ET.fromstring(text)
    out = {}
    for dep in root.iter("dependency"):
        g, a = dep.findtext("groupId"), dep.findtext("artifactId")
        if g and a:
            out[f"{g}:{a}"] = dep.findtext("version") or "*"
    return out


def _gradle(text: str) -> dict[str, str]:
    out = {}
    pattern = (
        r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|kapt|ksp)"
        r"""\s*\(?\s*['"]([^:'"]+):([^:'"]+)(?::([^'"]+))?['"]"""
    )
    for m in re.finditer(pattern, text):
        out[f"{m.group(1)}:{m.group(2)}"] = m.group(3) or "*"
    return out


def _csproj(text: str) -> dict[str, str]:
    out = {}
    for m in re.finditer(r'<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]+)")?', text):
        out[m.group(1)] = m.group(2) or "*"
    return out


def _pubspec(text: str) -> dict[str, str]:
    data = yaml.safe_load(text) or {}
    out = {}
    for sec in ("dependencies", "dev_dependencies"):
        for name, ver in (data.get(sec) or {}).items():
            out[name] = ver if isinstance(ver, str) else "*"
    return out


MANIFESTS = {
    "package.json": ("npm", _pkg_json),
    "composer.json": ("composer", _composer),
    "pyproject.toml": ("python", _pyproject),
    "Pipfile": ("python", _pipfile),
    "go.mod": ("go", _gomod),
    "Gemfile": ("ruby", _gemfile),
    "Cargo.toml": ("rust", _cargo),
    "pom.xml": ("maven", _pom),
    "build.gradle": ("gradle", _gradle),
    "build.gradle.kts": ("gradle", _gradle),
    "pubspec.yaml": ("dart", _pubspec),
}


def _manifest_kind(path: str):
    name = PurePosixPath(path).name
    if name in MANIFESTS:
        return MANIFESTS[name]
    if re.fullmatch(r"requirements([-_.][\w.-]+)?\.txt", name):
        return ("python", _requirements)
    if name.endswith(".csproj"):
        return ("nuget", _csproj)
    return None


def scan_dependencies(repo: Repo, files: dict[str, str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    candidates = sorted(
        (p for p in files if not _ignored(p) and _manifest_kind(p)), key=lambda p: p.count("/")
    )
    for path in candidates[:MAX_MANIFESTS]:
        eco, parser = _manifest_kind(path)
        text = repo.read(path)
        if text is None:
            continue
        try:
            packages = parser(text)
        except Exception as e:  # manifiestos rotos o exóticos: se anotan y se sigue
            result[path] = {"ecosystem": eco, "packages": {}, "error": str(e)[:200]}
            continue
        result[path] = {"ecosystem": eco, "packages": packages}
    return result


def all_packages(deps: dict[str, dict]) -> set[str]:
    return {name for m in deps.values() for name in m.get("packages", {})}


# =========================================================================
#  Frameworks
# =========================================================================
# nombre de paquete (o prefijo terminado en *) -> framework
FRAMEWORK_PACKAGES = {
    # PHP
    "laravel/framework": "Laravel",
    "laravel/lumen-framework": "Lumen",
    "symfony/framework-bundle": "Symfony",
    "symfony/http-kernel": "Symfony (componentes)",
    "slim/slim": "Slim",
    "cakephp/cakephp": "CakePHP",
    "codeigniter4/framework": "CodeIgniter",
    "yiisoft/yii2": "Yii2",
    "laminas/laminas-mvc": "Laminas",
    "livewire/livewire": "Livewire",
    "inertiajs/inertia-laravel": "Inertia (Laravel)",
    "filament/filament": "Filament",
    "laravel/nova": "Laravel Nova",
    "laravel/octane": "Laravel Octane",
    "api-platform/core": "API Platform",
    "drupal/core": "Drupal",
    "johnpbloch/wordpress": "WordPress",
    "roots/wordpress": "WordPress",
    "magento/product-community-edition": "Magento",
    "shopware/core": "Shopware",
    "prestashop/prestashop": "PrestaShop",
    "phpunit/phpunit": "PHPUnit",
    "pestphp/pest": "Pest",
    # JS / TS
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "@angular/core": "Angular",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "solid-js": "SolidJS",
    "astro": "Astro",
    "@remix-run/react": "Remix",
    "react-native": "React Native",
    "expo": "Expo",
    "electron": "Electron",
    "@ionic/core": "Ionic",
    "@capacitor/core": "Capacitor",
    "express": "Express",
    "@nestjs/core": "NestJS",
    "fastify": "Fastify",
    "koa": "Koa",
    "hono": "Hono",
    "@hapi/hapi": "hapi",
    "gatsby": "Gatsby",
    "vite": "Vite",
    "webpack": "Webpack",
    "tailwindcss": "Tailwind CSS",
    "bootstrap": "Bootstrap",
    "@mui/material": "Material UI",
    "jquery": "jQuery",
    "@reduxjs/toolkit": "Redux Toolkit",
    "redux": "Redux",
    "zustand": "Zustand",
    "@tanstack/react-query": "TanStack Query",
    "graphql": "GraphQL",
    "@apollo/client": "Apollo Client",
    "@apollo/server": "Apollo Server",
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "typeorm": "TypeORM",
    "sequelize": "Sequelize",
    "mongoose": "Mongoose",
    "drizzle-orm": "Drizzle",
    "jest": "Jest",
    "vitest": "Vitest",
    "cypress": "Cypress",
    "@playwright/test": "Playwright",
    "storybook": "Storybook",
    "typescript": "TypeScript",
    "socket.io": "Socket.IO",
    "three": "Three.js",
    "@vercel/ai": "Vercel AI SDK",
    "ai": "Vercel AI SDK",
    "langchain": "LangChain",
    "@langchain/core": "LangChain",
    # Python
    "django": "Django",
    "djangorestframework": "Django REST Framework",
    "flask": "Flask",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "tornado": "Tornado",
    "aiohttp": "aiohttp",
    "celery": "Celery",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "pandas": "pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "streamlit": "Streamlit",
    "scrapy": "Scrapy",
    "pytest": "pytest",
    "airflow": "Airflow",
    "apache-airflow": "Airflow",
    "llama-index": "LlamaIndex",
    # JVM / otros
    "org.springframework.boot:spring-boot-starter": "Spring Boot",
    "org.springframework.boot:spring-boot-starter-web": "Spring Boot (web)",
    "io.quarkus:quarkus-core": "Quarkus",
    "io.micronaut:micronaut-runtime": "Micronaut",
    "rails": "Ruby on Rails",
    "sinatra": "Sinatra",
    "github.com/gin-gonic/gin": "Gin",
    "github.com/labstack/echo/v4": "Echo",
    "github.com/gofiber/fiber/v2": "Fiber",
    "actix-web": "Actix Web",
    "axum": "Axum",
    "flutter": "Flutter",
    "Microsoft.AspNetCore.App": "ASP.NET Core",
}

FRAMEWORK_FILES = {
    "artisan": "Laravel",
    "manage.py": "Django",
    "angular.json": "Angular",
    "nuxt.config.*": "Nuxt",
    "next.config.*": "Next.js",
    "svelte.config.*": "Svelte",
    "astro.config.*": "Astro",
    "wp-config.php": "WordPress",
    "wp-config-sample.php": "WordPress",
    "symfony.lock": "Symfony",
    "pubspec.yaml": "Flutter/Dart",
    "Podfile": "iOS (CocoaPods)",
    "*.xcodeproj/project.pbxproj": "iOS (Xcode)",
    "AndroidManifest.xml": "Android",
    "serverless.yml": "Serverless Framework",
    "vercel.json": "Vercel",
    "netlify.toml": "Netlify",
}


def scan_frameworks(files: dict[str, str], deps: dict[str, dict]) -> list[str]:
    found: set[str] = set()
    for pkg in all_packages(deps):
        fw = FRAMEWORK_PACKAGES.get(pkg) or FRAMEWORK_PACKAGES.get(pkg.lower())
        if fw:
            found.add(fw)
    for path in files:
        if _ignored(path):
            continue
        name = PurePosixPath(path).name
        for pattern, fw in FRAMEWORK_FILES.items():
            if fnmatch(name, pattern) or ("/" in pattern and fnmatch(path, "*" + pattern)):
                found.add(fw)
    return sorted(found)


# =========================================================================
#  Servicios externos
# =========================================================================
SERVICE_PACKAGES = [
    (
        r"^(aws-sdk|@aws-sdk/|aws/aws-sdk-php|boto3|botocore|github.com/aws/|software.amazon.awssdk)",
        "AWS",
    ),
    (
        r"^(@google-cloud/|google/cloud|google-cloud-|googleapis|google/apiclient|firebase|@firebase/|kreait/)",
        "Google Cloud / Firebase",
    ),
    (r"^(@azure/|azure-|microsoft/azure|Azure\.)", "Azure"),
    (r"(^|/)stripe", "Stripe"),
    (r"paypal|braintree", "PayPal/Braintree"),
    (r"redsys", "Redsys"),
    (r"adyen", "Adyen"),
    (r"mollie", "Mollie"),
    (r"(^|/)twilio", "Twilio"),
    (r"sendgrid", "SendGrid"),
    (r"mailgun", "Mailgun"),
    (r"postmark", "Postmark"),
    (r"mailchimp|mandrill", "Mailchimp"),
    (r"brevo|sendinblue", "Brevo"),
    (r"(^|/)resend", "Resend"),
    (r"sentry", "Sentry"),
    (r"bugsnag", "Bugsnag"),
    (r"datadog|dd-trace|ddtrace", "Datadog"),
    (r"newrelic", "New Relic"),
    (r"opentelemetry", "OpenTelemetry"),
    (r"rollbar", "Rollbar"),
    (r"(^|/)(openai)", "OpenAI"),
    (r"anthropic", "Anthropic"),
    (r"(^|/)@?google/(genai|generative-ai)|google-genai", "Google Gemini"),
    (r"mistral", "Mistral"),
    (r"cohere", "Cohere"),
    (r"langchain|langgraph", "LangChain"),
    (r"pinecone", "Pinecone"),
    (r"weaviate", "Weaviate"),
    (r"qdrant", "Qdrant"),
    (r"algolia", "Algolia"),
    (r"meilisearch", "Meilisearch"),
    (r"elasticsearch|elastic/", "Elasticsearch"),
    (r"opensearch", "OpenSearch"),
    (r"(^|/)redis|predis|ioredis", "Redis"),
    (r"memcache", "Memcached"),
    (r"mongodb|mongoose", "MongoDB"),
    (r"kafka", "Kafka"),
    (r"amqp|rabbitmq", "RabbitMQ"),
    (r"pusher", "Pusher"),
    (r"(^|[/@])ably($|[/-])", "Ably"),
    (r"auth0", "Auth0"),
    (r"(^|[/@])okta", "Okta"),
    (r"keycloak", "Keycloak"),
    (r"cognito", "AWS Cognito"),
    (r"supabase", "Supabase"),
    (r"cloudinary", "Cloudinary"),
    (r"contentful", "Contentful"),
    (r"strapi", "Strapi"),
    (r"(^|[/@])sanity", "Sanity"),
    (r"shopify", "Shopify"),
    (r"hubspot", "HubSpot"),
    (r"salesforce|jsforce", "Salesforce"),
    (r"zendesk", "Zendesk"),
    (r"intercom", "Intercom"),
    (r"(^|[/@])segment(io)?($|[/-])", "Segment"),
    (r"mixpanel", "Mixpanel"),
    (r"amplitude", "Amplitude"),
    (r"posthog", "PostHog"),
    (r"launchdarkly", "LaunchDarkly"),
    (r"(^|[/@])unleash", "Unleash"),
    (r"growthbook", "GrowthBook"),
    (r"(^|[/@-])slack", "Slack"),
    (r"telegram", "Telegram"),
    (r"whatsapp", "WhatsApp"),
    (r"(^|/)vonage|nexmo", "Vonage"),
    (r"onesignal", "OneSignal"),
    (r"mapbox", "Mapbox"),
    (r"googlemaps|google-maps|@googlemaps", "Google Maps"),
    (r"dropbox", "Dropbox"),
    (r"docusign", "DocuSign"),
    (r"pdfmonkey|docraptor", "Servicio PDF"),
    (r"vercel", "Vercel"),
    (r"cloudflare", "Cloudflare"),
    (r"bitbucket|atlassian|jira", "Atlassian"),
    (r"holded", "Holded"),
    (r"(^|[/@-])(odoo|quickbooks|xero|sage-?one)", "ERP/contabilidad"),
]
_SERVICE_RES = [(re.compile(p, re.I), name) for p, name in SERVICE_PACKAGES]

HOST_RE = r"https?://[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}"
NOISE_HOSTS = re.compile(
    r"(localhost|127\.0\.0\.1|example\.(com|org|net)|\.test$|\.local$|\.lan$|w3\.org|schema\.org|"
    r"xmlns|json-schema\.org|purl\.org|opensource\.org|apache\.org|gnu\.org|mozilla\.org|"
    r"github\.com|githubusercontent|gitlab\.com|bitbucket\.org|npmjs\.(com|org)|packagist\.org|pypi\.org|"
    r"php\.net|python\.org|stackoverflow|wikipedia|fonts\.(googleapis|gstatic)\.com|cdnjs|jsdelivr|"
    r"unpkg|getbootstrap|jquery\.com|fontawesome|creativecommons|semver\.org|keepachangelog|"
    r"readthedocs|docs\.|developer\.mozilla|swagger\.io|openapis\.org|laravel\.com|symfony\.com|"
    r"reactjs\.org|react\.dev|vuejs\.org|angular\.io|nodejs\.org|yarnpkg|placehold|placeholder|"
    r"lorempixel|picsum|gravatar|youtube\.com|youtu\.be|vimeo\.com|twitter\.com|x\.com|facebook\.com|"
    r"instagram\.com|linkedin\.com|google\.com/(search|maps\?))",
    re.I,
)
SOURCE_GLOBS = [
    "*.php",
    "*.js",
    "*.jsx",
    "*.ts",
    "*.tsx",
    "*.vue",
    "*.svelte",
    "*.py",
    "*.go",
    "*.rb",
    "*.java",
    "*.kt",
    "*.cs",
    "*.rs",
    "*.dart",
    "*.swift",
    "*.yml",
    "*.yaml",
    "*.json",
    "*.env.example",
    ".env.example",
    "*.ini",
    "*.conf",
    "*.toml",
    "*.twig",
    "*.blade.php",
]
SOURCE_PATHSPECS = (
    SOURCE_GLOBS
    + [f":(exclude,glob)**/{d}**" for d in IGNORED_DIRS]
    + [
        ":(exclude,glob)**/*lock*",
        ":(exclude,glob)**/*.min.*",
        ":(exclude,glob)**/tests/**",
        ":(exclude,glob)**/test/**",
        ":(exclude,glob)**/__tests__/**",
        ":(exclude,glob)**/fixtures/**",
    ]
)


def _compose_images(repo: Repo, files: dict[str, str]) -> set[str]:
    images: set[str] = set()
    for path in files:
        name = PurePosixPath(path).name
        if _ignored(path) or not re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", name):
            continue
        try:
            data = yaml.safe_load(repo.read(path) or "") or {}
        except yaml.YAMLError:
            continue
        for svc in (data.get("services") or {}).values():
            if isinstance(svc, dict) and isinstance(svc.get("image"), str):
                images.add(svc["image"].split(":")[0].split("@")[0])
    return images


def scan_services(
    repo: Repo, files: dict[str, str], deps: dict[str, dict]
) -> dict[str, list[str]]:
    sdks: set[str] = set()
    for pkg in all_packages(deps):
        for rx, name in _SERVICE_RES:
            if rx.search(pkg):
                sdks.add(name)
                break
    hosts: set[str] = set()
    for match in repo.grep_matches(HOST_RE, SOURCE_PATHSPECS):
        host = match.split("://", 1)[1].lower().rstrip(".")
        if not NOISE_HOSTS.search(host) and not re.fullmatch(r"[\d.]+", host):
            hosts.add(host)
    return {
        "sdks": sorted(sdks),
        "hosts": sorted(hosts)[:400],
        "images": sorted(_compose_images(repo, files)),
    }


# =========================================================================
#  Prácticas, infraestructura y configuración
# =========================================================================
TEST_PATTERNS = [
    r"(^|/)tests?/",
    r"(^|/)__tests__/",
    r"(^|/)spec/",
    r"\.(test|spec)\.[jt]sx?$",
    r"Test\.php$",
    r"(^|/)test_[^/]+\.py$",
    r"_test\.(py|go)$",
    r"Tests?\.(java|kt|cs)$",
    r"(^|/)cypress/",
    r"(^|/)e2e/",
    r"(^|/)playwright",
]
_TEST_RES = [re.compile(p) for p in TEST_PATTERNS]

AI_TOOLING = [
    "CLAUDE.md",
    ".claude/*",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    ".cursor/*",
    ".github/copilot-instructions.md",
    ".github/instructions/*",
    ".github/prompts/*",
    ".windsurfrules",
    ".windsurf/*",
    ".aider*",
    ".continue/*",
    ".mcp.json",
    "mcp.json",
    ".junie/*",
    ".clinerules*",
    ".roo/*",
    ".kiro/*",
    "llms.txt",
    ".codex/*",
    "prompts/*",
]
QUALITY = [
    "phpstan.neon*",
    "psalm.xml*",
    ".php-cs-fixer*",
    "phpcs.xml*",
    "rector.php",
    "phpunit.xml*",
    "pest.php",
    ".eslintrc*",
    "eslint.config.*",
    ".prettierrc*",
    "prettier.config.*",
    "biome.json*",
    "tsconfig.json",
    ".stylelintrc*",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".flake8",
    "tox.ini",
    ".pre-commit-config.yaml",
    ".editorconfig",
    ".husky/*",
    "commitlint.config.*",
    ".golangci.y*ml",
    "sonar-project.properties",
    "jest.config.*",
    "vitest.config.*",
    "cypress.config.*",
    "playwright.config.*",
    "infection.json*",
    ".codeclimate.yml",
    "renovate.json",
    ".github/dependabot.yml",
]
CI = [
    "bitbucket-pipelines.yml",
    ".github/workflows/*",
    ".gitlab-ci.yml",
    "Jenkinsfile",
    ".circleci/*",
    "azure-pipelines.yml",
    ".drone.yml",
    ".travis.yml",
    "buildspec.yml",
    "cloudbuild.yaml",
    "appspec.yml",
    "Procfile",
    "deploy.php",
    "Envoy.blade.php",
    "fly.toml",
    "render.yaml",
    "app.yaml",
]
CONTAINERS = [
    "Dockerfile*",
    "*.Dockerfile",
    "docker-compose*.y*ml",
    "compose*.y*ml",
    ".devcontainer/*",
    "Vagrantfile",
    ".lando.yml",
    ".ddev/*",
    "Containerfile",
    "skaffold.yaml",
    "Tiltfile",
    "devbox.json",
    "flake.nix",
    "shell.nix",
    ".dockerignore",
]
IAC = [
    "*.tf",
    "*.tfvars",
    "Chart.yaml",
    "values*.yaml",
    "kustomization.y*ml",
    "k8s/*",
    "kubernetes/*",
    "helm/*",
    "ansible.cfg",
    "playbook*.y*ml",
    "*.pulumi.*",
    "Pulumi.yaml",
    "serverless.y*ml",
    "template.yaml",
    "cdk.json",
    "terragrunt.hcl",
    "nomad/*",
    "*.nomad",
]
CONFIG = [
    *CI,
    "Dockerfile*",
    "docker-compose*.y*ml",
    "compose*.y*ml",
    ".env.example",
    ".env.dist",
    "config/*.php",
    "config/*.y*ml",
    "config/packages/*.y*ml",
    "settings.py",
    "nginx*.conf",
    "*.nginx",
    ".htaccess",
    "php.ini",
    "supervisord.conf",
    "*.service",
    "crontab",
    "Makefile",
    "webpack.config.*",
    "vite.config.*",
    "next.config.*",
    "nuxt.config.*",
    "*.tf",
]

DOCBLOCK_LANGS = ["*.php", "*.js", "*.jsx", "*.ts", "*.tsx", "*.java", "*.kt", "*.cs"]
CODE_EXT = re.compile(r"\.(php|jsx?|tsx?|vue|svelte|py|go|rb|java|kt|cs|rs|dart|swift)$")


def _match_any(path: str, patterns: list[str]) -> bool:
    name = PurePosixPath(path).name
    for pat in patterns:
        if "/" in pat:
            if fnmatch(path, pat) or fnmatch(path, "*/" + pat):
                return True
        elif fnmatch(name, pat):
            return True
    return False


def scan_signals(repo: Repo, files: dict[str, str], with_docblocks: bool = True) -> dict:
    paths = [p for p in files if not _ignored(p)]
    code = [p for p in paths if CODE_EXT.search(p)]
    tests = [p for p in code if any(rx.search(p) for rx in _TEST_RES)]
    signals = {
        "files": len(paths),
        "code_files": len(code),
        "test_files": len(tests),
        "ai_tooling": sorted(p for p in paths if _match_any(p, AI_TOOLING))[:100],
        "quality": sorted(p for p in paths if _match_any(p, QUALITY))[:100],
        "ci": sorted(p for p in paths if _match_any(p, CI))[:100],
        "containers": sorted(p for p in paths if _match_any(p, CONTAINERS))[:100],
        "iac": sorted(p for p in paths if _match_any(p, IAC))[:100],
        "docblocks": 0,
        "typed_docblocks": 0,
    }
    if with_docblocks and code:
        excludes = [f":(exclude,glob)**/{d}**" for d in IGNORED_DIRS]
        signals["docblocks"] = repo.grep_count(r"^[[:space:]]*/\*\*", DOCBLOCK_LANGS + excludes)
        signals["typed_docblocks"] = repo.grep_count(
            r"@(param|return|throws|var)([^a-zA-Z]|$)", DOCBLOCK_LANGS + excludes
        )
    return signals


def scan_config(files: dict[str, str]) -> dict[str, str]:
    """{ruta: blob_sha} de archivos de configuración relevantes (para detectar cambios)."""
    return {p: sha for p, sha in files.items() if not _ignored(p) and _match_any(p, CONFIG)}


def base_images(repo: Repo, files: dict[str, str]) -> list[str]:
    images = set()
    for path in files:
        if _ignored(path) or not fnmatch(PurePosixPath(path).name, "*Dockerfile*"):
            continue
        for m in re.finditer(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", repo.read(path) or "", re.M | re.I):
            if not m.group(1).startswith("$"):
                images.add(m.group(1))
    return sorted(images)
