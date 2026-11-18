#!/usr/bin/env bash
# Build and deploy ONLY the StoreLimitless Vite frontend.
# Frontend project: drive-bot
# Frontend domain:  storelimitless.vercel.app
# Backend domain is used only as the API URL; this script never deploys or relinks it.

set -Eeuo pipefail

SYSTEM_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PATH="${PATH:+${PATH}:}${SYSTEM_PATH}"
export PATH

log() {
    printf '\n\033[1;32m==> %s\033[0m\n' "$1"
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${1:-}" ]]; then
    REPO_ROOT="$(cd -- "$1" && pwd)"
elif [[ -f "$SCRIPT_DIR/frontend/package.json" ]]; then
    REPO_ROOT="$SCRIPT_DIR"
elif [[ -f "$PWD/frontend/package.json" ]]; then
    REPO_ROOT="$(cd -- "$PWD" && pwd)"
else
    cat >&2 <<'MESSAGE'
Could not find the StoreLimitless repository (expected frontend/package.json).
Place build-vercel.sh in the repository root and run ./build-vercel.sh, or pass
an explicit repository path: ./build-vercel.sh /path/to/Store-Limitless
MESSAGE
    exit 1
fi

FRONTEND_DIR="$REPO_ROOT/frontend"
ROOT_VERCEL_DIR="$REPO_ROOT/.vercel"
PROJECT_JSON="$ROOT_VERCEL_DIR/project.json"
FRONTEND_DOMAIN="${VERCEL_FRONTEND_DOMAIN:-storelimitless.vercel.app}"
BACKEND_DOMAIN="${VERCEL_BACKEND_DOMAIN:-storelimitlessbackend.vercel.app}"
BACKEND_API_URL="https://${BACKEND_DOMAIN}"
FRONTEND_PROJECT="${VERCEL_FRONTEND_PROJECT:-drive-bot}"
VERCEL_SCOPE="${VERCEL_SCOPE:-}"

[[ -f "$FRONTEND_DIR/package.json" ]] || fail "frontend/package.json was not found under $REPO_ROOT"
[[ -f "$FRONTEND_DIR/vercel.json" ]] || fail "frontend/vercel.json was not found; expected the StoreLimitless Vite frontend"

for tool in node npm git; do
    command -v "$tool" >/dev/null 2>&1 || fail "Required tool '$tool' is not installed or not on PATH"
done

# Use the installed CLI when available, otherwise use npx to get the Vercel CLI.
vercel_cli() {
    if command -v vercel >/dev/null 2>&1; then
        vercel "$@"
    else
        npx --yes vercel@latest "$@"
    fi
}

log "Checking tools and Vercel authentication"
printf 'Node: %s\n' "$(node --version)"
printf 'npm:  %s\n' "$(npm --version)"
if ! vercel_cli whoami >/dev/null 2>&1; then
    echo "You are not logged into Vercel. Starting Vercel login..."
    vercel_cli login
fi

# Keep the local Vercel link and pulled production env file out of Git.
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_EXCLUDE="$(git -C "$REPO_ROOT" rev-parse --git-path info/exclude)"
    if [[ "$GIT_EXCLUDE" != /* ]]; then GIT_EXCLUDE="$REPO_ROOT/$GIT_EXCLUDE"; fi
    mkdir -p "$(dirname -- "$GIT_EXCLUDE")"
    touch "$GIT_EXCLUDE"
    for exclude_pattern in '/.vercel/' '/frontend/.vercel/' '/frontend/.env.production.local'; do
        if ! grep -Fqx "$exclude_pattern" "$GIT_EXCLUDE"; then
            printf '%s\n' "$exclude_pattern" >> "$GIT_EXCLUDE"
        fi
    done
fi

project_name_from_link() {
    local project_json="$1"
    [[ -f "$project_json" ]] || return 0
    node -e 'try { process.stdout.write(String(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).projectName || "")); } catch {}' "$project_json"
}

log "Verifying the frontend Vercel project only"
mkdir -p "$ROOT_VERCEL_DIR"

inspect_args=(project inspect "$FRONTEND_PROJECT")
link_args=(link --yes --project "$FRONTEND_PROJECT")
if [[ -n "$VERCEL_SCOPE" ]]; then
    inspect_args+=(--scope "$VERCEL_SCOPE")
    link_args+=(--scope "$VERCEL_SCOPE")
fi

if ! (cd "$REPO_ROOT" && vercel_cli "${inspect_args[@]}") >/dev/null; then
    fail "Could not verify existing frontend Vercel project '$FRONTEND_PROJECT'. Check the project name, account, and VERCEL_SCOPE."
fi

CURRENT_PROJECT="$(project_name_from_link "$PROJECT_JSON")"
if [[ "${CURRENT_PROJECT,,}" != "${FRONTEND_PROJECT,,}" ]]; then
    log "Linking repository root to existing frontend project '$FRONTEND_PROJECT'"
    (cd "$REPO_ROOT" && vercel_cli "${link_args[@]}")
    CURRENT_PROJECT="$(project_name_from_link "$PROJECT_JSON")"
fi
[[ "${CURRENT_PROJECT,,}" == "${FRONTEND_PROJECT,,}" ]] || fail "Root Vercel link is not '$FRONTEND_PROJECT'; stopping to avoid deploying the wrong project."
printf 'Frontend project: %s (domain %s)\n' "$FRONTEND_PROJECT" "$FRONTEND_DOMAIN"
printf 'Backend API URL (unchanged): %s\n' "$BACKEND_API_URL"

log "Installing locked frontend dependencies"
(cd "$FRONTEND_DIR" && npm ci)

log "Pulling frontend production environment variables"
env_pull_args=(env pull frontend/.env.production.local --environment=production --yes)
if [[ -n "$VERCEL_SCOPE" ]]; then env_pull_args+=(--scope "$VERCEL_SCOPE"); fi
(cd "$REPO_ROOT" && vercel_cli "${env_pull_args[@]}")
chmod 600 "$FRONTEND_DIR/.env.production.local" 2>/dev/null || true

log "Building the Vite frontend locally"
# Force the existing stable backend URL into the bundle. The backend is not deployed or modified.
(cd "$FRONTEND_DIR" && VITE_API_URL="$BACKEND_API_URL" npm run build)
[[ -f "$FRONTEND_DIR/dist/index.html" ]] || fail "Vite did not produce frontend/dist/index.html; deployment stopped"

log "Preparing Vercel static Build Output for the frontend"
rm -rf "$ROOT_VERCEL_DIR/output"
mkdir -p "$ROOT_VERCEL_DIR/output/static"
cp -a "$FRONTEND_DIR/dist/." "$ROOT_VERCEL_DIR/output/static/"

# Build Output API v3: serve files first, then send SPA routes to index.html.
cat > "$ROOT_VERCEL_DIR/output/config.json" <<'JSON'
{
  "version": 3,
  "routes": [
    { "handle": "filesystem" },
    { "src": "/(.*)", "dest": "/index.html" }
  ]
}
JSON

[[ -f "$ROOT_VERCEL_DIR/output/config.json" ]] || fail "Failed to create frontend Vercel output configuration"
[[ -f "$ROOT_VERCEL_DIR/output/static/index.html" ]] || fail "Frontend Vercel output is missing index.html"

log "Deploying ONLY the frontend to Vercel production"
deploy_args=(deploy --prebuilt --prod --yes)
if [[ -n "$VERCEL_SCOPE" ]]; then deploy_args+=(--scope "$VERCEL_SCOPE"); fi

if ! DEPLOY_OUTPUT="$(cd "$REPO_ROOT" && vercel_cli "${deploy_args[@]}" 2>&1)"; then
    printf '%s\n' "$DEPLOY_OUTPUT" >&2
    fail "Frontend deployment failed. No backend deployment was attempted."
fi
printf '%s\n' "$DEPLOY_OUTPUT"

DEPLOYMENT_URL="$(printf '%s\n' "$DEPLOY_OUTPUT" \
    | grep -Eo 'https://[A-Za-z0-9.-]+\.vercel\.app' \
    | grep -Fvx "https://${FRONTEND_DOMAIN}" \
    | grep -Fvx "https://${BACKEND_DOMAIN}" \
    | tail -n 1 || true)"
[[ -n "$DEPLOYMENT_URL" ]] || fail "Could not extract the frontend deployment URL from Vercel CLI output."

log "Assigning the frontend production domain"
alias_args=(alias set "$DEPLOYMENT_URL" "$FRONTEND_DOMAIN")
if [[ -n "$VERCEL_SCOPE" ]]; then alias_args+=(--scope "$VERCEL_SCOPE"); fi
(cd "$REPO_ROOT" && vercel_cli "${alias_args[@]}")

cat <<MESSAGE

Frontend deployment completed:
  Frontend: https://${FRONTEND_DOMAIN}
  API:      ${BACKEND_API_URL}

Only the '$FRONTEND_PROJECT' Vercel project was deployed.
The backend project was not inspected, relinked, built, or deployed.
No git commit or git push was performed.
MESSAGE
