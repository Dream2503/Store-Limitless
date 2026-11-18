#!/usr/bin/env bash
# Build and deploy both StoreLimitless Vercel applications:
#   drive-bot      -> React/Vite frontend
#   drive-bot-nema -> Next.js API proxy

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
BACKEND_DIR="$REPO_ROOT/backend/storelimitlessbackend"
FRONTEND_VERCEL_DIR="$FRONTEND_DIR/.vercel"
BACKEND_VERCEL_DIR="$BACKEND_DIR/.vercel"
FRONTEND_PROJECT_JSON="$FRONTEND_VERCEL_DIR/project.json"
BACKEND_PROJECT_JSON="$BACKEND_VERCEL_DIR/project.json"
FRONTEND_ENV_FILE="$FRONTEND_DIR/.env.production.local"
BACKEND_ENV_FILE="$BACKEND_DIR/.env.production.local"
FRONTEND_DOMAIN="${VERCEL_FRONTEND_DOMAIN:-storelimitless.vercel.app}"
BACKEND_DOMAIN="${VERCEL_BACKEND_DOMAIN:-cdn-storelimitless.vercel.app}"
BACKEND_API_URL="https://${BACKEND_DOMAIN}"
FRONTEND_PROJECT="${VERCEL_FRONTEND_PROJECT:-drive-bot}"
BACKEND_PROJECT="${VERCEL_BACKEND_PROJECT:-drive-bot-nema}"
VERCEL_SCOPE="${VERCEL_SCOPE:-}"
EXPECTED_VERCEL_USER="${VERCEL_EXPECTED_USER:-}"

[[ -f "$FRONTEND_DIR/package.json" ]] || fail "frontend/package.json was not found under $REPO_ROOT"
[[ -f "$FRONTEND_DIR/package-lock.json" ]] || fail "frontend/package-lock.json is required for a reproducible frontend install"
[[ -f "$FRONTEND_DIR/vercel.json" ]] || fail "frontend/vercel.json was not found; expected the StoreLimitless Vite frontend"
[[ -f "$BACKEND_DIR/package.json" ]] || fail "backend/storelimitlessbackend/package.json was not found"
[[ -f "$BACKEND_DIR/package-lock.json" ]] || fail "backend/storelimitlessbackend/package-lock.json is required for a reproducible API proxy install"
[[ -f "$BACKEND_DIR/vercel.json" ]] || fail "backend/storelimitlessbackend/vercel.json was not found"

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

run_vercel_in() {
    local working_dir="$1"
    shift
    (cd "$working_dir" && vercel_cli "$@")
}

project_name_from_link() {
    local project_json="$1"
    [[ -f "$project_json" ]] || return 0
    node -e 'try { process.stdout.write(String(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).projectName || "")); } catch {}' "$project_json"
}

project_id_from_link() {
    local project_json="$1"
    [[ -f "$project_json" ]] || return 0
    node -e 'try { process.stdout.write(String(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).projectId || "")); } catch {}' "$project_json"
}

ensure_project_link() {
    local working_dir="$1"
    local project_json="$2"
    local project_name="$3"
    local label="$4"
    local inspect_args=(project inspect "$project_name")
    local create_args=(project create "$project_name")
    local link_args=(link --yes --project "$project_name")

    if [[ -n "$VERCEL_SCOPE" ]]; then
        inspect_args+=(--scope "$VERCEL_SCOPE")
        create_args+=(--scope "$VERCEL_SCOPE")
        link_args+=(--scope "$VERCEL_SCOPE")
    fi

    log "Verifying the $label Vercel project '$project_name'"
    if ! run_vercel_in "$working_dir" "${inspect_args[@]}" >/dev/null 2>&1; then
        log "Project '$project_name' was not found in the selected Vercel scope; creating it"
        if ! run_vercel_in "$working_dir" "${create_args[@]}"; then
            fail "Could not create Vercel project '$project_name'. Check the project name, scope, and account permissions. You can override it with the corresponding VERCEL_*_PROJECT variable."
        fi
    fi

    # Each app has its own .vercel/project.json. Never share a link context
    # between the frontend and API proxy in this monorepo.
    log "Linking the $label directory to '$project_name'"
    run_vercel_in "$working_dir" "${link_args[@]}"

    local linked_name linked_id
    linked_name="$(project_name_from_link "$project_json")"
    linked_id="$(project_id_from_link "$project_json")"
    [[ "${linked_name,,}" == "${project_name,,}" ]] || fail "$label directory is linked to '$linked_name', not '$project_name'; refusing to deploy the wrong project."
    [[ -n "$linked_id" ]] || fail "Vercel did not write a projectId to $project_json; refusing to deploy."
    printf '%s project: %s (project ID %s)\n' "$label" "$project_name" "$linked_id"
}

pull_production_environment() {
    local working_dir="$1"
    local env_file="$2"
    local label="$3"
    local env_pull_args=(env pull .env.production.local --environment=production --yes)
    if [[ -n "$VERCEL_SCOPE" ]]; then env_pull_args+=(--scope "$VERCEL_SCOPE"); fi

    log "Pulling $label production environment variables"
    run_vercel_in "$working_dir" "${env_pull_args[@]}"
    chmod 600 "$env_file" 2>/dev/null || true
}

extract_deployment_url() {
    local deployment_output="$1"
    printf '%s\n' "$deployment_output" \
        | grep -Eo 'https://[A-Za-z0-9.-]+\.vercel\.app' \
        | grep -Fvx "https://${FRONTEND_DOMAIN}" \
        | grep -Fvx "https://${BACKEND_DOMAIN}" \
        | tail -n 1 || true
}

DEPLOYMENT_URL_RESULT=""
deploy_prebuilt_project() {
    local working_dir="$1"
    local project_name="$2"
    local label="$3"
    local deploy_args=(deploy --prebuilt --prod --yes)
    local deployment_output deployment_url
    if [[ -n "$VERCEL_SCOPE" ]]; then deploy_args+=(--scope "$VERCEL_SCOPE"); fi

    log "Deploying the $label project '$project_name' to Vercel production"
    if ! deployment_output="$(run_vercel_in "$working_dir" "${deploy_args[@]}" 2>&1)"; then
        printf '%s\n' "$deployment_output" >&2
        fail "$label deployment failed. The other project will not be deployed after this failure."
    fi
    printf '%s\n' "$deployment_output"

    deployment_url="$(extract_deployment_url "$deployment_output")"
    [[ -n "$deployment_url" ]] || fail "Could not extract the $label deployment URL from Vercel CLI output."
    DEPLOYMENT_URL_RESULT="$deployment_url"
}

assign_production_domain() {
    local working_dir="$1"
    local deployment_url="$2"
    local domain="$3"
    local label="$4"
    local alias_args=(alias set "$deployment_url" "$domain")
    if [[ -n "$VERCEL_SCOPE" ]]; then alias_args+=(--scope "$VERCEL_SCOPE"); fi

    log "Assigning $label production domain '$domain'"
    run_vercel_in "$working_dir" "${alias_args[@]}"
}

log "Checking tools and Vercel authentication"
printf 'Node: %s\n' "$(node --version)"
printf 'npm:  %s\n' "$(npm --version)"
if ! vercel_cli whoami >/dev/null 2>&1; then
    echo "You are not logged into Vercel. Starting Vercel login..."
    vercel_cli login
fi

AUTHENTICATED_USER="$(vercel_cli whoami 2>/dev/null | tr -d '\r' | tail -n 1)"
[[ -n "$AUTHENTICATED_USER" ]] || fail "Could not determine the authenticated Vercel user."
printf 'Vercel account: %s\n' "$AUTHENTICATED_USER"

if [[ -n "$EXPECTED_VERCEL_USER" && "${AUTHENTICATED_USER,,}" != "${EXPECTED_VERCEL_USER,,}" ]]; then
    fail "Authenticated as '$AUTHENTICATED_USER', but VERCEL_EXPECTED_USER is '$EXPECTED_VERCEL_USER'. Run 'vercel logout' then 'vercel login' with the intended account."
fi

if [[ -n "$VERCEL_SCOPE" ]]; then
    printf 'Vercel deployment scope: %s\n' "$VERCEL_SCOPE"
else
    printf 'Vercel deployment scope: personal scope for %s\n' "$AUTHENTICATED_USER"
fi

# Keep per-project Vercel links and pulled production env files out of Git.
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_EXCLUDE="$(git -C "$REPO_ROOT" rev-parse --git-path info/exclude)"
    if [[ "$GIT_EXCLUDE" != /* ]]; then GIT_EXCLUDE="$REPO_ROOT/$GIT_EXCLUDE"; fi
    mkdir -p "$(dirname -- "$GIT_EXCLUDE")"
    touch "$GIT_EXCLUDE"
    for exclude_pattern in \
        '/frontend/.vercel/' \
        '/backend/storelimitlessbackend/.vercel/' \
        '/frontend/.env.production.local' \
        '/backend/storelimitlessbackend/.env.production.local'; do
        if ! grep -Fqx "$exclude_pattern" "$GIT_EXCLUDE"; then
            printf '%s\n' "$exclude_pattern" >> "$GIT_EXCLUDE"
        fi
    done
fi

# Link each project from its own app directory. This prevents the Vercel CLI
# from using drive-bot's project ID while building or deploying drive-bot-nema.
ensure_project_link "$BACKEND_DIR" "$BACKEND_PROJECT_JSON" "$BACKEND_PROJECT" "API proxy"
ensure_project_link "$FRONTEND_DIR" "$FRONTEND_PROJECT_JSON" "$FRONTEND_PROJECT" "frontend"

log "Installing locked API proxy dependencies"
(cd "$BACKEND_DIR" && npm ci)

log "Installing locked frontend dependencies"
(cd "$FRONTEND_DIR" && npm ci)

pull_production_environment "$BACKEND_DIR" "$BACKEND_ENV_FILE" "API proxy"
pull_production_environment "$FRONTEND_DIR" "$FRONTEND_ENV_FILE" "frontend"

log "Building the Next.js API proxy for Vercel production"
run_vercel_in "$BACKEND_DIR" build --prod
[[ -f "$BACKEND_VERCEL_DIR/output/config.json" ]] || fail "Vercel did not produce backend .vercel/output/config.json; API proxy deployment stopped"

log "Building the Vite frontend locally"
# The browser continues using the stable proxy for metadata/control requests.
# Only file chunk PUT bodies discover and use the current Quick Tunnel directly.
(cd "$FRONTEND_DIR" && VITE_API_URL="$BACKEND_API_URL" npm run build)

log "Preparing Vercel static Build Output for the frontend"
rm -rf "$FRONTEND_VERCEL_DIR/output"
mkdir -p "$FRONTEND_VERCEL_DIR/output/static"
cp -a "$FRONTEND_DIR/dist/." "$FRONTEND_VERCEL_DIR/output/static/"

# Build Output API v3: serve files first, then send SPA routes to index.html.
cat > "$FRONTEND_VERCEL_DIR/output/config.json" <<'JSON'
{
  "version": 3,
  "routes": [
    { "handle": "filesystem" },
    { "src": "/(.*)", "dest": "/index.html" }
  ]
}
JSON

[[ -f "$FRONTEND_VERCEL_DIR/output/config.json" ]] || fail "Failed to create frontend Vercel output configuration"
[[ -f "$FRONTEND_VERCEL_DIR/output/static/index.html" ]] || fail "Frontend Vercel output is missing index.html"

# Deploy the API proxy first so /api/current-upstream exists before the new
# frontend starts calling it for direct file uploads.
deploy_prebuilt_project "$BACKEND_DIR" "$BACKEND_PROJECT" "API proxy"
BACKEND_DEPLOYMENT_URL="$DEPLOYMENT_URL_RESULT"
assign_production_domain "$BACKEND_DIR" "$BACKEND_DEPLOYMENT_URL" "$BACKEND_DOMAIN" "API proxy"

deploy_prebuilt_project "$FRONTEND_DIR" "$FRONTEND_PROJECT" "frontend"
FRONTEND_DEPLOYMENT_URL="$DEPLOYMENT_URL_RESULT"
assign_production_domain "$FRONTEND_DIR" "$FRONTEND_DEPLOYMENT_URL" "$FRONTEND_DOMAIN" "frontend"

cat <<MESSAGE

Both StoreLimitless Vercel deployments completed:
  Frontend:  https://${FRONTEND_DOMAIN}  ($FRONTEND_PROJECT)
  API proxy: https://${BACKEND_DOMAIN}  ($BACKEND_PROJECT)

The API proxy was deployed first, followed by the frontend.
The Cloudflare Quick Tunnel registration flow and its runtime URL remain unchanged.
No git commit or git push was performed.
MESSAGE
