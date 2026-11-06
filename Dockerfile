# Démonstrateur humatheque-catalogage (SPEC §10).
# Construction : docker compose build   (ou docker build -t humatheque .)
FROM python:3.12-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    HUMATHEQUE_RACINE=/app \
    REACCENTUE_CACHE=/opt/humatheque/reaccentue-cache.pz \
    HOST=0.0.0.0

WORKDIR /app

# uv, version figée (installé par pip : pas de dépendance à un registre d'images tiers)
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir uv==0.11.32

# Certificat d'autorité facultatif, pour construire derrière un proxy qui
# re-chiffre TLS : docker build --secret id=ca,src=/chemin/ca.crt …
# Sans ce secret, rien ne change.

# Dépendances (couche mise en cache tant que uv.lock ne change pas)
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev --no-install-project

# Application, configuration, spécification
COPY src ./src
COPY config ./config
COPY regles ./regles
COPY reference/moteur-regles/exemple_contexte.json ./reference/moteur-regles/
COPY CLAUDE.md ./
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    uv sync --frozen --no-dev

# Cache du dictionnaire reaccentue, construit au build (SPEC §4.1)
RUN mkdir -p /opt/humatheque && uv run --no-sync humatheque cache-reaccentue

# Feuille de style : exécutable autonome Tailwind, version figée (SPEC §10)
ARG TAILWIND_VERSION=v3.4.17
ARG TARGETARCH
RUN --mount=type=secret,id=ca,required=false \
    arch=$([ "${TARGETARCH:-amd64}" = "arm64" ] && echo arm64 || echo x64) && \
    python -c "import os, ssl, sys, urllib.request as u; \
ca = '/run/secrets/ca' if os.path.exists('/run/secrets/ca') else None; \
ctx = ssl.create_default_context(cafile=ca); \
open('/usr/local/bin/tailwindcss', 'wb').write(u.urlopen(sys.argv[1], context=ctx).read())" \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${arch}" && \
    chmod +x /usr/local/bin/tailwindcss && \
    tailwindcss -c src/humatheque/web/tailwind/tailwind.config.js \
      -i src/humatheque/web/tailwind/input.css -o src/humatheque/web/static/css/app.css --minify

EXPOSE 8000
VOLUME ["/app/data"]
CMD ["uv", "run", "--no-sync", "humatheque", "serve", "--host", "0.0.0.0", "--port", "8000"]
