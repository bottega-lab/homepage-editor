#!/usr/bin/env bash
# Homepage Editor v1.1.0 - first-run setup. SPDX-License-Identifier: MIT
#
# Asks for the paths, Homepage's address and port, the editor's port and login,
# then writes .env next to this script. Run it again any time to change settings:
# your current values are offered as the defaults.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
APP_DIR="$(pwd)"
ENV_FILE="$APP_DIR/.env"

say()  { printf '%s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }
ask() {  # ask VAR "Question" "default"
  local var="$1" q="$2" def="${3:-}" ans
  if [ -n "$def" ]; then read -r -p "$q [$def]: " ans || true; else read -r -p "$q: " ans || true; fi
  printf -v "$var" '%s' "${ans:-$def}"
}
yes_no() {  # yes_no "Question" Y|N
  local ans; read -r -p "$1 [$( [ "$2" = Y ] && echo Y/n || echo y/N )]: " ans || true
  ans="${ans:-$2}"; [[ "$ans" =~ ^[Yy] ]]
}

# Read one value from the existing .env (only plain KEY=value or KEY='value' lines).
old() {
  [ -f "$ENV_FILE" ] || return 0
  local line; line="$(grep -E "^$1=" "$ENV_FILE" | tail -n1 || true)"
  line="${line#*=}"; line="${line#\'}"; line="${line%\'}"; printf '%s' "$line"
}

DOCKER=""
if docker info >/dev/null 2>&1; then DOCKER="docker"
elif command -v sudo >/dev/null && sudo -n docker info >/dev/null 2>&1; then DOCKER="sudo docker"
fi

port_in_use() {
  local p="$1"
  if command -v ss >/dev/null && ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]$p\$"; then return 0; fi
  if [ -n "$DOCKER" ] && $DOCKER ps --format '{{.Ports}}' | grep -Eq "[:]$p->"; then return 0; fi
  return 1
}
free_port() { local p="$1"; while port_in_use "$p"; do p=$((p+1)); done; echo "$p"; }

used_subnets() { [ -n "$DOCKER" ] && $DOCKER network inspect $($DOCKER network ls -q) \
  --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr ' ' '\n' | grep . || true; }
host_subnets() {  # networks this server itself uses (LAN, VLANs, VPN): a Docker network must not overlap them
  command -v ip >/dev/null || return 0
  ip -4 route show 2>/dev/null | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\/[0-9]+$/ {print $1}' || true
}
valid_cidr() {  # IPv4 a.b.c.d/n
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})/([0-9]{1,2})$ ]] || return 1
  local i; for i in 1 2 3 4; do [ "${BASH_REMATCH[$i]}" -le 255 ] || return 1; done
  [ "${BASH_REMATCH[5]}" -ge 8 ] && [ "${BASH_REMATCH[5]}" -le 29 ]
}
ip2int() { local a b c d; IFS=. read -r a b c d <<<"$1"; echo $(( (a<<24) + (b<<16) + (c<<8) + d )); }
overlaps() {  # overlaps A/m B/n -> true if the two IPv4 ranges share any address
  local a="${1%/*}" m="${1#*/}" b="${2%/*}" n="${2#*/}" k mask
  [[ "$b" == *:* ]] && return 1                       # IPv6: cannot overlap an IPv4 range
  k=$(( m < n ? m : n )); mask=$(( (0xFFFFFFFF << (32 - k)) & 0xFFFFFFFF ))
  [ $(( $(ip2int "$a") & mask )) -eq $(( $(ip2int "$b") & mask )) ]
}
subnet_clash() {  # subnet_clash CIDR -> prints the first used network it overlaps (Docker or the host)
  local s
  while read -r s; do [ -n "$s" ] && overlaps "$1" "$s" && { echo "$s"; return 0; }; done \
    < <(used_subnets; host_subnets)
  return 1
}
free_subnet() {  # first 10.0.N.0/24 that overlaps no Docker network and none of the host's networks
  local n
  [ -n "$DOCKER" ] || return 0                        # cannot check: leave it to Docker
  for n in $(seq 1 254); do
    subnet_clash "10.0.$n.0/24" >/dev/null || { echo "10.0.$n.0/24"; return; }
  done
}

# ---- Time zone picker: Enter keeps the default, "?" shows region/city lists,
#      any other text is checked as a name or used to search (e.g. "rome", "new york").
TZ_ALL="$(timedatectl list-timezones 2>/dev/null || true)"
if [ -z "$TZ_ALL" ] && [ -d /usr/share/zoneinfo ]; then
  TZ_ALL="$(cd /usr/share/zoneinfo && find . -type f | sed 's|^\./||')"
fi
TZ_ALL="$(grep -E '^(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/[A-Za-z]' <<<"$TZ_ALL" | sort -u || true)"
[ -n "$TZ_ALL" ] && TZ_ALL="$TZ_ALL"$'\n'"UTC"

tz_valid() { [ "$1" = UTC ] || grep -qxF -- "$1" <<<"$TZ_ALL"; }

menu() {  # numbered list in columns
  local i=1 w=0 it cols
  for it in "$@"; do [ "${#it}" -gt "$w" ] && w=${#it}; done
  cols=$(( ${COLUMNS:-$(tput cols 2>/dev/null || echo 80)} / (w + 8) )); [ "$cols" -lt 1 ] && cols=1
  for it in "$@"; do
    printf '%5d) %-*s' "$i" "$w" "$it"; [ $((i % cols)) -eq 0 ] && echo; i=$((i+1))
  done
  [ $(( (i-1) % cols )) -ne 0 ] && echo
  return 0
}

pick_from() {  # pick_from "zones (one per line)" "label" "prefix to hide" -> sets TZ
  local zones n
  if [ -z "$1" ]; then warn "No time zone $2. Try again, or type ? for the list."; return 1; fi
  mapfile -t zones <<<"$1"
  if [ "${#zones[@]}" -eq 1 ]; then
    yes_no "   Use ${zones[0]}?" Y && { TZ="${zones[0]}"; return 0; }; return 1
  fi
  say "   Time zones $2:"; menu "${zones[@]#"$3"}"
  read -r -p "   Number (Enter = back): " n || true
  [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#zones[@]}" ] || return 1
  TZ="${zones[n-1]}"; say "   Chosen: $TZ"
}

pick_region() {
  local regions n r
  mapfile -t regions < <(cut -d/ -f1 <<<"$TZ_ALL" | sort -u)
  say "   Regions:"; menu "${regions[@]}"
  read -r -p "   Region number (Enter = back): " n || true
  [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] && [ "$n" -le "${#regions[@]}" ] || return 1
  r="${regions[n-1]}"
  [ "$r" = UTC ] && { TZ=UTC; return 0; }
  pick_from "$(grep "^$r/" <<<"$TZ_ALL")" "in $r" "$r/"
}

choose_tz() {  # choose_tz default -> sets TZ
  local def="$1" ans
  [ -n "$TZ_ALL" ] && ! tz_valid "$def" && def=UTC
  while :; do
    say "   Time zone for backup dates: Enter = keep, ? = choose from a list, or type a city to search"
    read -r -p "   Time zone [$def]: " ans || true
    ans="${ans:-$def}"
    if [ -z "$TZ_ALL" ]; then
      warn "No time zone list on this system, so '$ans' could not be checked."; TZ="$ans"; return
    fi
    if [ "$ans" = "?" ]; then pick_region && return; continue; fi
    if tz_valid "$ans"; then TZ="$ans"; return; fi
    pick_from "$(grep -iF -- "${ans// /_}" <<<"$TZ_ALL" || true)" "matching '$ans'" "" && return
  done
}

say "Homepage Editor - setup"
say "======================="
[ -f "$ENV_FILE" ] && say "Found an existing .env: its values are shown as defaults (press Enter to keep)."
[ -z "$DOCKER" ] && warn "Could not talk to Docker, so ports and networks can only be checked partly."
say ""

# ---- 1. Homepage's config folder
# hp_check DIR -> prints "strong", "likely" or "no: <reason>".
#   Required: services.yaml + settings.yaml + bookmarks.yaml or widgets.yaml (Homepage creates
#   these itself), services.yaml shaped as a list of groups, settings.yaml not a list.
#   "strong" = also Homepage's own traces: logs/homepage.log, or its "gethomepage.dev" comments.
first_line() { grep -vE '^[[:space:]]*(#|$)|^---' "$1" 2>/dev/null | head -n1 || true; }
hp_check() {
  local d="$1" l
  [ -f "$d/services.yaml" ] && [ -f "$d/settings.yaml" ] || { echo "no: services.yaml or settings.yaml missing"; return; }
  [ -f "$d/bookmarks.yaml" ] || [ -f "$d/widgets.yaml" ] || { echo "no: no bookmarks.yaml or widgets.yaml"; return; }
  l="$(first_line "$d/services.yaml")"
  [ -z "$l" ] || [[ "$l" =~ ^-[[:space:]].*:[[:space:]]*$ || "$l" =~ ^\[\]$ ]] || { echo "no: services.yaml is not a list of groups"; return; }
  l="$(first_line "$d/settings.yaml")"
  [[ "$l" == -* ]] && { echo "no: settings.yaml is a list, not settings"; return; }
  if [ -f "$d/logs/homepage.log" ] || grep -qs "gethomepage.dev" "$d"/{services,settings,bookmarks,widgets}.yaml; then
    echo strong
  else echo likely; fi
}

# Candidates: one line per folder, "path|homepage port on the host|where it was found"
CANDIDATES=""
add_candidate() { grep -qF -- "$1|" <<<"$CANDIDATES" || CANDIDATES+="$1|$2|$3"$'\n'; }

detect_docker() {  # Homepage containers (running or stopped) and the folder mounted as their config
  [ -n "$DOCKER" ] || return 0
  local id img name state env cdir mounts src d iport port net
  while read -r id img name state; do
    case "$img" in *gethomepage/homepage*|*benphelps/homepage*) ;; *) continue ;; esac
    env="$($DOCKER inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$id")"
    cdir="$(sed -n 's/^HOMEPAGE_CONFIG_DIR=//p' <<<"$env" | tail -n1)"
    mounts="$($DOCKER inspect -f '{{range .Mounts}}{{.Destination}}|{{.Source}}{{println}}{{end}}' "$id")"
    src=""
    for d in ${cdir:+"${cdir%/}"} /app/config /config; do
      src="$(awk -F'|' -v d="$d" '$1==d {print $2; exit}' <<<"$mounts")"; [ -n "$src" ] && break
    done
    if [ -z "$src" ]; then warn "Homepage container '$name' has no config folder mounted from the server; skipped."; continue; fi
    iport="$(sed -n 's/^PORT=//p' <<<"$env" | tail -n1)"; iport="${iport:-3000}"
    net="$($DOCKER inspect -f '{{.HostConfig.NetworkMode}}' "$id")"
    if [ "$net" = host ]; then port="$iport"
    else
      port="$($DOCKER inspect -f '{{range $p, $b := .HostConfig.PortBindings}}{{$p}}={{range $b}}{{.HostPort}} {{end}}{{println}}{{end}}' "$id" \
        | awk -F= -v p="$iport/tcp" '$1==p {split($2, a, " "); print a[1]; exit}')"
    fi
    add_candidate "${src%/}" "$port" "container '$name' ($state)"
  done < <($DOCKER ps -a --format '{{.ID}} {{.Image}} {{.Names}} {{.State}}')
}

search_folders() {  # fallback: look for Homepage-like config folders in usual places
  local root f d v roots=("$HOME" /opt /srv /docker /data)
  [ -n "${SUDO_USER:-}" ] && roots+=("$(getent passwd "$SUDO_USER" | cut -d: -f6)")
  say "   Searching for a Homepage config folder (a few seconds)..."
  for root in "${roots[@]}"; do
    [ -d "$root" ] || continue
    while IFS= read -r f; do
      d="$(dirname "$f")"; v="$(hp_check "$d")"
      [[ "$v" == no* ]] || add_candidate "$d" "" "search ($([ "$v" = strong ] && echo "Homepage's own files found" || echo "file names match only"))"
    done < <(timeout 30 find "$root" -maxdepth 5 \
        \( -name node_modules -o -name .git -o -name .cache -o -name backups -o -name proc \
           -o -regex '.*/[0-9]\{4\}-[0-9][0-9]-[0-9][0-9]_[0-9][0-9]-[0-9][0-9]-[0-9][0-9].*' \) -prune \
        -o -type f -name services.yaml -print 2>/dev/null || true)
  done
  # "Homepage's own files found" first
  CANDIDATES="$(grep -F "own files" <<<"$CANDIDATES" || true)"$'\n'"$(grep -vF "own files" <<<"$CANDIDATES" || true)"
  CANDIDATES="$(grep . <<<"$CANDIDATES" || true)"
}

DETECTED_PORT=""
CFG_DEF="$(old HOMEPAGE_CONFIG)"
if [ -z "$CFG_DEF" ]; then
  detect_docker
  [ -z "$CANDIDATES" ] && search_folders
  CANDIDATES="$(grep . <<<"$CANDIDATES" || true)"
  if [ -n "$CANDIDATES" ]; then
    mapfile -t CAND <<<"$CANDIDATES"
    if [ "${#CAND[@]}" -eq 1 ]; then
      IFS='|' read -r CFG_DEF DETECTED_PORT how <<<"${CAND[0]}"
      say "   Found Homepage's config folder via $how:"
      say "     $CFG_DEF${DETECTED_PORT:+   (Homepage port $DETECTED_PORT)}"
    else
      say "   Found more than one possible Homepage config folder:"
      i=1; for c in "${CAND[@]}"; do IFS='|' read -r p hp how <<<"$c"
        printf '%5d) %s%s\n          via %s\n' "$i" "$p" "${hp:+   (port $hp)}" "$how"; i=$((i+1)); done
      while :; do
        read -r -p "   Number to use (Enter = 1, 0 = type a path yourself): " n || true; n="${n:-1}"
        [ "$n" = 0 ] && break
        if [[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -le "${#CAND[@]}" ]; then
          IFS='|' read -r CFG_DEF DETECTED_PORT how <<<"${CAND[n-1]}"; break; fi
        warn "Please type a number from the list."
      done
    fi
    [[ "${how:-}" == search* ]] && warn "Found by searching, not from a Homepage container. Please check it is the right folder."
  else
    say "   No Homepage container or config folder found; please type the path."
  fi
fi

while :; do
  ask HOMEPAGE_CONFIG "1. Full path of Homepage's config folder (has services.yaml, settings.yaml)" "$CFG_DEF"
  HOMEPAGE_CONFIG="${HOMEPAGE_CONFIG%/}"
  if [[ "$HOMEPAGE_CONFIG" != /* ]]; then warn "Please give a full path starting with /"; continue; fi
  if [ ! -d "$HOMEPAGE_CONFIG" ]; then warn "Folder not found: $HOMEPAGE_CONFIG"; continue; fi
  v="$(hp_check "$HOMEPAGE_CONFIG")"
  if [[ "$v" == no* ]]; then
    warn "This does not look like a Homepage config folder (${v#no: })."
    yes_no "   Use it anyway?" N || continue
  fi
  if [ "$HOMEPAGE_CONFIG" != "$CFG_DEF" ]; then
    # Another folder than suggested: use the port of the container that mounts this folder, or,
    # if there is exactly one Homepage container, its port (e.g. when testing on a copy).
    DETECTED_PORT="$(awk -F'|' -v p="$HOMEPAGE_CONFIG" '$1==p {print $2; exit}' <<<"$CANDIDATES")"
    if [ -z "$DETECTED_PORT" ]; then
      ONLY="$(grep -F "|container '" <<<"$CANDIDATES" || true)"
      [ "$(grep -c . <<<"$ONLY")" -eq 1 ] && DETECTED_PORT="$(cut -d'|' -f2 <<<"$ONLY")"
    fi
  fi
  break
done
PUID_DEF="$(old PUID)"; PUID_DEF="${PUID_DEF:-$(stat -c %u "$HOMEPAGE_CONFIG")}"
PGID_DEF="$(old PGID)"; PGID_DEF="${PGID_DEF:-$(stat -c %g "$HOMEPAGE_CONFIG")}"

# ---- 2. Backups folder
B_DEF="$(old BACKUP_PATH)"; B_DEF="${B_DEF:-$APP_DIR/backups}"
while :; do
  ask BACKUP_PATH "2. Full path for the dated backups (keep it OUTSIDE the config folder)" "$B_DEF"
  BACKUP_PATH="${BACKUP_PATH%/}"
  if [[ "$BACKUP_PATH" != /* ]]; then warn "Please give a full path starting with /"; continue; fi
  if [[ "$BACKUP_PATH/" == "$HOMEPAGE_CONFIG/"* ]]; then warn "That is inside Homepage's config folder. Choose another place."; continue; fi
  break
done
DATA_PATH="$(old DATA_PATH)"; DATA_PATH="${DATA_PATH:-$APP_DIR/data}"   # editor's own settings (Configure)
K_DEF="$(old BACKUP_KEEP)"
ask BACKUP_KEEP "   How many backups to keep (0 = keep all)" "${K_DEF:-100}"
[[ "$BACKUP_KEEP" =~ ^[0-9]+$ ]] || { warn "Not a number, using 100."; BACKUP_KEEP=100; }

# ---- 3. Homepage's address and port
say ""
P_DEF="$(old HOMEPAGE_PROTOCOL)"
ask HOMEPAGE_PROTOCOL "3. Homepage protocol (http or https)" "${P_DEF:-http}"
[[ "$HOMEPAGE_PROTOCOL" =~ ^https?$ ]] || { warn "Using http."; HOMEPAGE_PROTOCOL=http; }
H_DEF="$(old HOMEPAGE_HOST)"; H_DEF="${H_DEF:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
ask HOMEPAGE_HOST "   Homepage IP address or host name (no http://, no port)" "$H_DEF"
HOMEPAGE_HOST="${HOMEPAGE_HOST#*://}"; HOMEPAGE_HOST="${HOMEPAGE_HOST%%/*}"
if [[ "$HOMEPAGE_HOST" == *:* && "$HOMEPAGE_HOST" != \[* ]]; then
  warn "Removed ':${HOMEPAGE_HOST##*:}' from the host; give the port in the next question."
  HOMEPAGE_HOST="${HOMEPAGE_HOST%:*}"
fi
# Port: Enter keeps the suggestion, 0 = no port in the link (browser uses 80 for http, 443 for https).
if [ -f "$ENV_FILE" ] && grep -qE "^HOMEPAGE_PORT=(''|)$" "$ENV_FILE"; then
  HP_DEF=0                                   # saved earlier as "no port"
else
  HP_DEF="$(old HOMEPAGE_PORT)"
  if [ -z "$HP_DEF" ] && [ -n "$DETECTED_PORT" ]; then
    HP_DEF="$DETECTED_PORT"; say "   (port $DETECTED_PORT taken from the Homepage container)"
  elif [ -z "$HP_DEF" ]; then
    HP_DEF=3000
    [ "$HOMEPAGE_PROTOCOL" = https ] && say "   (3000 is Homepage's own port; behind a reverse proxy with https you usually want 0)"
  fi
fi
while :; do
  say "   Homepage port: Enter = keep, 0 = no port in the link (uses 80 for http, 443 for https)"
  ask HOMEPAGE_PORT "   Homepage port" "$HP_DEF"
  [ "$HOMEPAGE_PORT" = 0 ] && { HOMEPAGE_PORT=""; break; }
  [[ "$HOMEPAGE_PORT" =~ ^[0-9]+$ && "$HOMEPAGE_PORT" -ge 1 && "$HOMEPAGE_PORT" -le 65535 ]] && break
  warn "Port must be a number from 1 to 65535, or 0 for no port."
done

# ---- 4. How to get the app, editor port, name and login
say ""
IMAGE_DEF="$(old IMAGE)"; IMAGE_DEF="${IMAGE_DEF:-ghcr.io/bottega-lab/homepage-editor:latest}"
M_DEF="$(old COMPOSE_FILE)"; if [[ "$M_DEF" == *build* ]]; then M_DEF=2; elif [ -n "$M_DEF" ]; then M_DEF=1; else M_DEF=""; fi
IMAGE_OK=""
if [[ "$IMAGE_DEF" == *YOUR-GITHUB-NAME* ]]; then
  IMAGE_NOTE="the ready image is not published yet"
elif [ -n "$DOCKER" ] && timeout 20 $DOCKER manifest inspect "$IMAGE_DEF" >/dev/null 2>&1; then
  IMAGE_OK=1; IMAGE_NOTE="found $IMAGE_DEF"
else
  IMAGE_NOTE="could not reach $IMAGE_DEF"
fi
[ -d "$APP_DIR/editor" ] || BUILD_IMPOSSIBLE=1
say "4. How to get the app:"
say "   1 = ready image from GitHub (amd64 + arm64, e.g. Raspberry Pi with a 64-bit system)."
say "       Updates with 'docker compose pull' or Watchtower.   ($IMAGE_NOTE)"
say "   2 = build it here from the source in this folder (you can read every line first)."
say "       Updates with 'git pull' and 'docker compose up -d --build'."
[ -z "$M_DEF" ] && { [ -n "$IMAGE_OK" ] || [ -n "${BUILD_IMPOSSIBLE:-}" ] && M_DEF=1 || M_DEF=2; }
while :; do
  ask MODE_ANS "   Choose 1 or 2" "$M_DEF"
  case "$MODE_ANS" in
    1) INSTALL=image; break ;;
    2) if [ -n "${BUILD_IMPOSSIBLE:-}" ]; then warn "The editor/ source folder is not here: use 1, or download the whole repository."; continue; fi
       INSTALL=build; break ;;
    *) warn "Type 1 or 2." ;;
  esac
done
if [ "$INSTALL" = image ]; then
  say "   Image version (the part after the colon): latest = always the newest version (default),"
  say "     :1 = all 1.x updates but never 2.0 by surprise, :1.1.0 = this exact version, no updates."
  ask IMAGE "   Image" "$IMAGE_DEF"
  [[ "$IMAGE" == *YOUR-GITHUB-NAME* ]] && warn "This image name is a placeholder: starting will fail until the real image name is set."
  COMPOSE_FILE="docker-compose.yml"
else
  IMAGE="$IMAGE_DEF"
  COMPOSE_FILE="docker-compose.yml:docker-compose.build.yml"
fi                                   # docker-compose.subnet.yml is added below if a network is chosen
STACK_DEF="$(old STACK_NAME)"; STACK_DEF="${STACK_DEF:-homepage-editor}"
if [ -z "$(old STACK_NAME)" ] && [ -n "$DOCKER" ] && $DOCKER ps -a --format '{{.Names}}' | grep -qx "$STACK_DEF"; then
  STACK_DEF="homepage-editor-$(basename "$APP_DIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"
  warn "A container called homepage-editor already exists; suggesting $STACK_DEF so both can run."
fi
while :; do
  ask STACK_NAME "4. Name for this stack and container" "$STACK_DEF"
  [[ "$STACK_NAME" =~ ^[a-z0-9][a-z0-9_-]+$ ]] && break
  warn "At least 2 characters: lowercase letters, digits, - and _ only."
done
OLD_PORT="$(old EDITOR_PORT)"
while :; do
  E_DEF="${OLD_PORT:-$(free_port 5005)}"
  ask EDITOR_PORT "   Port for the editor page" "$E_DEF"
  if ! [[ "$EDITOR_PORT" =~ ^[0-9]+$ && "$EDITOR_PORT" -ge 1 && "$EDITOR_PORT" -le 65535 ]]; then warn "Port must be a number from 1 to 65535."; continue; fi
  if port_in_use "$EDITOR_PORT" && [ "$EDITOR_PORT" != "$OLD_PORT" ]; then
    warn "Port $EDITOR_PORT is already in use. Next free one: $(free_port "$EDITOR_PORT")"; OLD_PORT=""; continue
  fi
  break
done
# Login: a login page (default), or none when something else already protects the editor
A_DEF="$(old AUTH_MODE)"; A_DEF="${A_DEF:-login}"
say "   Login: 1 = login page (recommended)"
say "          2 = no login: ONLY if something else protects the editor (Authelia, Authentik,"
say "              an NPM access list, a VPN...). Otherwise anyone on your network can edit"
say "              Homepage and see its API keys."
while :; do
  ask AUTH_ANS "   Choose 1 or 2" "$([ "$A_DEF" = off ] && echo 2 || echo 1)"
  case "$AUTH_ANS" in 1) AUTH_MODE=login; break ;; 2) AUTH_MODE=off; break ;; *) warn "Type 1 or 2." ;; esac
done
if [ "$AUTH_MODE" = off ] && [ "$A_DEF" != off ]; then
  yes_no "   Really switch the login off?" N || AUTH_MODE=login
fi
U_DEF="$(old EDITOR_USER)"
OLD_PW="$(old EDITOR_PASSWORD)"
# EDITOR_PASSWORD_HIDDEN (only used by setup.sh): yes = the stored password was made silently in
# "no login" mode and never shown. A password you typed or saw stays "no" and is kept.
OLD_HIDDEN="$(old EDITOR_PASSWORD_HIDDEN)"
if [ -z "$OLD_HIDDEN" ] && [ -n "$OLD_PW" ]; then
  # .env from 1.0 (no marker): only a password stored while the login was off can be a hidden one
  [ "$A_DEF" = off ] && OLD_HIDDEN=yes || OLD_HIDDEN=no
fi
if [ "$AUTH_MODE" = off ]; then
  # keep (or quietly create) a login, so it works right away if you switch it back on
  EDITOR_USER="${U_DEF:-admin}"
  if [ -n "$OLD_PW" ]; then EDITOR_PASSWORD="$OLD_PW"; PW_HIDDEN="$OLD_HIDDEN"   # keep it as it was
  else EDITOR_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 20 || true)"; PW_HIDDEN=yes; fi
  say "   No login. The editor shows a \"No login\" badge as a reminder."
else
ask EDITOR_USER "   Login user name" "${U_DEF:-admin}"
if grep -qs '"auth"' "$DATA_PATH/editor-settings.json"; then
  say "   Note: the login was changed in the editor (Settings). Enter keeps that login;"
  say "   typing a new password here replaces it (this is how a forgotten password is reset)."
fi
# A hidden password (never shown) is not offered as "keep current": Enter makes a new one and shows it.
[ "$OLD_HIDDEN" = yes ] && OLD_PW=""
while :; do
  if [ -n "$OLD_PW" ]; then
    read -r -s -p "   Login password (Enter = keep current): " EDITOR_PASSWORD || true; echo
    EDITOR_PASSWORD="${EDITOR_PASSWORD:-$OLD_PW}"
  else
    read -r -s -p "   Login password (Enter = generate one): " EDITOR_PASSWORD || true; echo
    if [ -z "$EDITOR_PASSWORD" ]; then
      EDITOR_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 20 || true)"
      say "   Generated password: $EDITOR_PASSWORD   (write it down; it is also in .env)"
    else
      read -r -s -p "   Repeat password: " PW2 || true; echo
      [ "$EDITOR_PASSWORD" = "$PW2" ] || { warn "Passwords differ, try again."; continue; }
    fi
  fi
  if [[ "$EDITOR_PASSWORD" == *"'"* ]]; then warn "Please don't use the ' character."; OLD_PW=""; continue; fi
  if [ "$EDITOR_PASSWORD" = "change-me" ] || [ "${#EDITOR_PASSWORD}" -lt 8 ]; then warn "Use at least 8 characters (and not change-me)."; OLD_PW=""; continue; fi
  break
done
PW_HIDDEN=no                         # typed, shown or kept-and-known
fi

# ---- 5. Details that are usually right automatically
say ""
TZ_DEF="$(old TZ)"; TZ_DEF="${TZ_DEF:-$(cat /etc/timezone 2>/dev/null || timedatectl show -p Timezone --value 2>/dev/null || echo UTC)}"
if [ -f "$ENV_FILE" ] && grep -q '^EDITOR_SUBNET=' "$ENV_FILE"; then S_DEF="$(old EDITOR_SUBNET)"; S_DEF="${S_DEF:-auto}"
else S_DEF="$(free_subnet)"; S_DEF="${S_DEF:-auto}"; fi
say "5. Checked automatically (press Enter to accept):"
ask PUID "   User id that owns the config files" "$PUID_DEF"
ask PGID "   Group id that owns the config files" "$PGID_DEF"
choose_tz "$TZ_DEF"
say "   Docker network: a free one is suggested (it overlaps no Docker network and none of this"
say "   server's own networks). auto = let Docker choose."
while :; do
  ask EDITOR_SUBNET "   Docker network for this stack" "$S_DEF"
  [ "$EDITOR_SUBNET" = auto ] && { EDITOR_SUBNET=""; break; }
  valid_cidr "$EDITOR_SUBNET" || { warn "Write it like 10.0.10.0/24 (or auto)."; continue; }
  if [ -n "$DOCKER" ] && [ "$EDITOR_SUBNET" != "$(old EDITOR_SUBNET)" ] && CLASH="$(subnet_clash "$EDITOR_SUBNET")"; then
    warn "$EDITOR_SUBNET overlaps $CLASH, which is already in use. Free one: $(free_subnet || true)"; continue
  fi
  [ -z "$DOCKER" ] && warn "Docker could not be asked, so $EDITOR_SUBNET was not checked. Make sure no other network uses it."
  break
done
[ -n "$EDITOR_SUBNET" ] && COMPOSE_FILE="$COMPOSE_FILE:docker-compose.subnet.yml"

# ---- Summary and write
URL="$HOMEPAGE_PROTOCOL://$HOMEPAGE_HOST${HOMEPAGE_PORT:+:$HOMEPAGE_PORT}"
say ""
say "Summary"
say "  Homepage config : $HOMEPAGE_CONFIG"
say "  Backups         : $BACKUP_PATH (keep $BACKUP_KEEP)"
say "  Editor settings : $DATA_PATH"
say "  Homepage link   : $URL"
say "  Editor          : $STACK_NAME on port $EDITOR_PORT, $([ "$AUTH_MODE" = off ] && echo "NO LOGIN" || echo "login page, user $EDITOR_USER")"
say "  Runs as         : $PUID:$PGID   time zone $TZ   network ${EDITOR_SUBNET:-chosen by Docker}"
say "  App             : $([ "$INSTALL" = image ] && echo "ready image $IMAGE" || echo "built here from the source")"
yes_no "Write these settings to .env?" Y || { say "Nothing written."; exit 1; }

if [ -f "$ENV_FILE" ]; then cp -p "$ENV_FILE" "$ENV_FILE.bak"; say "Previous .env saved as .env.bak"; fi
umask 077
cat >"$ENV_FILE" <<EOF
# ---- Homepage Editor settings (written by setup.sh on $(date '+%Y-%m-%d %H:%M')) ----
# Run ./setup.sh again to change them, or edit here. Then: docker compose up -d
# Values are in single quotes so characters like \$ and # are kept as they are.

# Folder of this app (used to find ./editor when the stack is started from elsewhere)
APP_DIR='$APP_DIR'
STACK_NAME='$STACK_NAME'

# How the app is installed: the ready image, or (with COMPOSE_FILE set) built from the source here
IMAGE='$IMAGE'
COMPOSE_FILE='$COMPOSE_FILE'

# Homepage's config folder, and where the dated backups go
HOMEPAGE_CONFIG='$HOMEPAGE_CONFIG'
BACKUP_PATH='$BACKUP_PATH'
# The editor's own settings (login, appearance... changed in Configure)
DATA_PATH='$DATA_PATH'
BACKUP_KEEP='$BACKUP_KEEP'

# Address of Homepage, for the "Open Homepage" link
HOMEPAGE_PROTOCOL='$HOMEPAGE_PROTOCOL'
HOMEPAGE_HOST='$HOMEPAGE_HOST'
HOMEPAGE_PORT='$HOMEPAGE_PORT'

# Editor page and login (AUTH_MODE: login = login page, off = no login at all)
EDITOR_PORT='$EDITOR_PORT'
AUTH_MODE='$AUTH_MODE'
EDITOR_USER='$EDITOR_USER'
EDITOR_PASSWORD='$EDITOR_PASSWORD'
# For setup.sh only: yes = this password was made in "no login" mode and never shown
EDITOR_PASSWORD_HIDDEN='$PW_HIDDEN'

# Owner of Homepage's config files, time zone, Docker network (empty = chosen by Docker;
# a value here is only used together with docker-compose.subnet.yml in COMPOSE_FILE)
PUID='$PUID'
PGID='$PGID'
TZ='$TZ'
EDITOR_SUBNET='$EDITOR_SUBNET'
EOF
umask 022
say "Wrote $ENV_FILE"

# ---- Backups and settings folders (must belong to the user the editor runs as)
for dir in "$BACKUP_PATH" "$DATA_PATH"; do
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir" 2>/dev/null || sudo mkdir -p "$dir"
    say "Created $dir"
  fi
  if [ "$(stat -c %u:%g "$dir")" != "$PUID:$PGID" ]; then
    say "$dir must belong to $PUID:$PGID."
    chown "$PUID:$PGID" "$dir" 2>/dev/null || sudo chown "$PUID:$PGID" "$dir" \
      || warn "Could not change the owner. Run: sudo chown $PUID:$PGID '$dir'"
  fi
done

# ---- Start
say ""
if [ "$INSTALL" = build ]; then UP="docker compose up -d --build"; else UP="docker compose pull && docker compose up -d"; fi
if [ -n "$DOCKER" ] && yes_no "Start the editor now?" Y; then
  if [ "$INSTALL" = build ]; then $DOCKER compose up -d --build; else $DOCKER compose pull && $DOCKER compose up -d; fi
  say ""
  say "Done. Open: http://$(hostname -I 2>/dev/null | awk '{print $1}'):$EDITOR_PORT$([ "$AUTH_MODE" = login ] && echo "  (user: $EDITOR_USER)")"
else
  say "To start later: cd '$APP_DIR' && $UP"
fi
