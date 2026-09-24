#!/usr/bin/env bash
# Internal deployment engine; deploy.sh provides the standalone installer/menu.
set -Eeuo pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
INSTALL=1
CONFIG_ARGS=(--existing "$PROJECT_DIR/.env")
DOCKER=(docker)
ROOT_CMD=()
ACME_REDIRECT_PORT=''
RUNTIME_ENV="$PROJECT_DIR/deploy/runtime.env"
ACME_HOME="$PROJECT_DIR/deploy/acme"
PLAN_FILE=''

usage() {
  cat <<'HELP'
TACO小剧场部署引擎（Linux；日常使用根目录 deploy.sh 菜单）
  bash scripts/deploy_stack.sh            部署当前源码，复用已有配置
  bash deploy.sh --domain video.your-domain.com --direct
  bash deploy.sh --domain video.your-domain.com --proxy socks5://proxy-host:1080
  bash deploy.sh --check                  只校验配置，不安装、构建或启动
  bash deploy.sh --no-install             缺少 Docker/Python 时直接报错

首次使用需将真实域名解析到服务器。80/443 空闲时由 Caddy 自动申请证书。
若本机已安装 3x-ui / xray 并占用 80 或 443，脚本不会停止这些服务，
改为复用已有证书或临时借用 80 做 HTTP-01 校验，再在空闲端口提供 HTTPS。
海外服务器使用 --direct；默认 overseas 需要容器可达的海外代理。
含密码的代理建议交互输入，避免保存到 Shell 历史。
Ubuntu/Debian 缺少 Docker 时通过官方 apt 源安装；其他 Linux 请先安装 Docker Compose。
脚本保留现有账号和数据卷；拉取源码并更新请用 bash deploy.sh --update。
HELP
}

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --domain|--proxy)
      (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || die "$1 缺少参数"
      CONFIG_ARGS+=("$1" "$2"); shift 2 ;;
    --direct|--reconfigure) CONFIG_ARGS+=("$1"); shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --no-install) INSTALL=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数 $1；使用 --help 查看说明" ;;
  esac
done

root_access() {
  if ((EUID != 0)); then
    command -v sudo >/dev/null || die '安装依赖需要 root 或 sudo。'
    ROOT_CMD=(sudo)
  fi
}

apt_system() {
  [[ -r /etc/os-release ]] || die '无法识别系统，请预先安装 Python 3 和 Docker Compose。'
  # This is the operating system's root-owned metadata, never the project .env.
  . /etc/os-release
  [[ "$ID" == ubuntu || "$ID" == debian ]] || die '自动安装支持 Ubuntu/Debian；其他系统请先安装 Python 3 和 Docker Compose。'
  root_access
}

install_docker() {
  apt_system
  local status package
  for package in docker.io docker-compose docker-compose-v2 docker-doc podman-docker containerd runc; do
    status="$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)"
    if [[ "$status" == 'install ok installed' ]]; then
      die "发现现有系统包 $package；请按 Docker 官方文档处理兼容问题，脚本不会移除现有容器软件。"
    fi
  done
  local codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [[ "$codename" =~ ^[a-z][a-z0-9-]*$ ]] || die '无法识别系统代号。'
  printf '正在通过 Docker 官方 apt 源安装容器运行环境…\n'
  "${ROOT_CMD[@]}" apt-get update
  "${ROOT_CMD[@]}" apt-get install -y ca-certificates curl
  "${ROOT_CMD[@]}" install -m 0755 -d /etc/apt/keyrings
  "${ROOT_CMD[@]}" curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  "${ROOT_CMD[@]}" chmod a+r /etc/apt/keyrings/docker.asc
  cat <<REPO | "${ROOT_CMD[@]}" tee /etc/apt/sources.list.d/docker.sources >/dev/null
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
REPO
  "${ROOT_CMD[@]}" apt-get update
  "${ROOT_CMD[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  "${ROOT_CMD[@]}" systemctl enable --now docker
}

iptables_bin() {
  if command -v iptables >/dev/null; then
    printf '%s\n' iptables
  elif command -v iptables-nft >/dev/null; then
    printf '%s\n' iptables-nft
  else
    return 1
  fi
}

ip6tables_bin() {
  if command -v ip6tables >/dev/null; then
    printf '%s\n' ip6tables
  elif command -v ip6tables-nft >/dev/null; then
    printf '%s\n' ip6tables-nft
  else
    return 1
  fi
}

clear_acme_redirect() {
  local port="${ACME_REDIRECT_PORT:-}"
  [[ -n "$port" ]] || return 0
  local ipt ip6
  ipt="$(iptables_bin 2>/dev/null || true)"
  ip6="$(ip6tables_bin 2>/dev/null || true)"
  if [[ -n "$ipt" ]]; then
    while "${ROOT_CMD[@]}" "$ipt" -t nat -C PREROUTING -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port" 2>/dev/null; do
      "${ROOT_CMD[@]}" "$ipt" -t nat -D PREROUTING -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port" || true
    done
    while "${ROOT_CMD[@]}" "$ipt" -C INPUT -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT 2>/dev/null; do
      "${ROOT_CMD[@]}" "$ipt" -D INPUT -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT || true
    done
  fi
  if [[ -n "$ip6" ]]; then
    while "${ROOT_CMD[@]}" "$ip6" -t nat -C PREROUTING -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port" 2>/dev/null; do
      "${ROOT_CMD[@]}" "$ip6" -t nat -D PREROUTING -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port" || true
    done
    while "${ROOT_CMD[@]}" "$ip6" -C INPUT -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT 2>/dev/null; do
      "${ROOT_CMD[@]}" "$ip6" -D INPUT -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT || true
    done
  fi
  ACME_REDIRECT_PORT=''
}

setup_acme_redirect() {
  local port="$1" ipt
  root_access
  ipt="$(iptables_bin)" || die '80 端口已被占用，且系统没有 iptables，无法临时转发 ACME 校验。'
  "${ROOT_CMD[@]}" "$ipt" -t nat -I PREROUTING 1 -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port"
  "${ROOT_CMD[@]}" "$ipt" -I INPUT 1 -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT
  ACME_REDIRECT_PORT="$port"
  local ip6
  if ip6="$(ip6tables_bin)"; then
    "${ROOT_CMD[@]}" "$ip6" -t nat -I PREROUTING 1 -p tcp --dport 80 -m comment --comment TACO_ACME_HTTP01 -j REDIRECT --to-ports "$port" 2>/dev/null || true
    "${ROOT_CMD[@]}" "$ip6" -I INPUT 1 -p tcp --dport "$port" -m comment --comment TACO_ACME_HTTP01 -j ACCEPT 2>/dev/null || true
  fi
}

find_acme() {
  if [[ -x "$ACME_HOME/acme.sh" && ! -L "$ACME_HOME/acme.sh" ]]; then
    printf '%s\n' "$ACME_HOME/acme.sh"
    return 0
  fi
  return 1
}

run_acme() {
  local acme="$1" httpport="$2"
  shift 2
  # Keep this project's acme.sh isolated from 3x-ui's installer copy.
  if ((EUID == 0)) || ((httpport >= 1024)); then
    HOME="$ACME_HOME" LE_WORKING_DIR="$ACME_HOME" "$acme" --home "$ACME_HOME" "$@"
  else
    root_access
    "${ROOT_CMD[@]}" env HOME="$ACME_HOME" LE_WORKING_DIR="$ACME_HOME" "$acme" --home "$ACME_HOME" "$@"
  fi
}

install_acme() {
  local installer
  installer="$(find_acme || true)"
  if [[ -n "$installer" ]]; then
    printf '%s\n' "$installer"
    return 0
  fi
  command -v curl >/dev/null || die '申请证书需要 curl 或已安装的 acme.sh。'
  printf '未找到独立证书工具，正在安装 acme.sh 到部署目录（不会改动 3x-ui）…\n'
  mkdir -p -- "$ACME_HOME"
  chmod 700 "$ACME_HOME"
  HOME="$ACME_HOME" curl -fsSL https://get.acme.sh | HOME="$ACME_HOME" LE_WORKING_DIR="$ACME_HOME" sh -s -- --install-online --home "$ACME_HOME" -m "taco@${DOMAIN}" >/dev/null
  installer="$(find_acme || true)"
  [[ -n "$installer" ]] || die 'acme.sh 安装失败。'
  printf '%s\n' "$installer"
}

copy_issued_cert() {
  local dest="$1" source key
  dest="${dest%/}"
  mkdir -p -- "$dest"
  chmod 700 "$dest"
  for source in \
    "$ACME_HOME/${DOMAIN}_ecc/fullchain.cer" \
    "$ACME_HOME/${DOMAIN}/fullchain.cer"
  do
    key="${source%/*}/${DOMAIN}.key"
    if [[ -f "$source" && -f "$key" && ! -L "$source" && ! -L "$key" ]]; then
      cp -- "$source" "$dest/fullchain.pem"
      cp -- "$key" "$dest/privkey.pem"
      chmod 600 "$dest/fullchain.pem" "$dest/privkey.pem"
      return 0
    fi
  done
  return 1
}

issue_certificate() {
  local dest="$1" acme httpport need_redirect="$2" acme_port="$3"
  dest="${dest%/}"
  mkdir -p -- "$dest"
  chmod 700 "$dest"
  if python3 "$PROJECT_DIR/scripts/https_compat.py" --domain "$DOMAIN" --copy-cert --dest "$dest" --extra-cert-dir "$dest"; then
    printf '使用服务器上已有的有效证书（3x-ui / acme.sh / 本地文件），未停止现有服务。\n'
    return 0
  fi
  printf '80/443 无法全部让给 Caddy，改为独立申请证书，不会停止 3x-ui。\n'
  acme="$(install_acme)"
  httpport=80
  if [[ "$need_redirect" == 1 ]]; then
    httpport="$acme_port"
    printf '检测到 80 已被占用，临时把公网 80 转到 %s 做证书校验，随后立即恢复。\n' "$httpport"
    setup_acme_redirect "$httpport"
  fi
  if ! run_acme "$acme" "$httpport" --set-default-ca --server letsencrypt >/dev/null; then
    clear_acme_redirect
    die '无法设置 Let’s Encrypt 为默认证书颁发机构。'
  fi
  local issued=0
  if run_acme "$acme" "$httpport" --issue -d "$DOMAIN" --standalone --httpport "$httpport" --listen-v4 --keylength ec-256; then
    issued=1
  elif run_acme "$acme" "$httpport" --issue -d "$DOMAIN" --standalone --httpport "$httpport" --listen-v4; then
    issued=1
  fi
  clear_acme_redirect
  ((issued)) || die "证书申请失败。请确认域名已解析到本机，防火墙放行 80，且 3x-ui 入站不要拦截 ACME 校验。也可把已有 fullchain.pem / privkey.pem 放到 $dest 后重试。"
  copy_issued_cert "$dest" || die '证书已签发，但未能复制到部署目录。'
  local reload
  printf -v reload 'bash %q' "$PROJECT_DIR/scripts/reload_caddy.sh"
  run_acme "$acme" 8443 --install-cert -d "$DOMAIN" --ecc --fullchain-file "$dest/fullchain.pem" --key-file "$dest/privkey.pem" --reloadcmd "$reload" >/dev/null 2>&1 \
    || run_acme "$acme" 8443 --install-cert -d "$DOMAIN" --fullchain-file "$dest/fullchain.pem" --key-file "$dest/privkey.pem" --reloadcmd "$reload" >/dev/null 2>&1 \
    || true
}

plan_https() {
  PLAN_FILE="$TEMP_DIR/https-plan.json"
  python3 "$PROJECT_DIR/scripts/https_compat.py" --plan --domain "$DOMAIN" --output "$PLAN_FILE" --write-runtime "$RUNTIME_ENV" --extra-cert-dir "$PROJECT_DIR/deploy/certs"
  plan_get() { python3 -c 'import json,sys; value=json.load(open(sys.argv[1]))[sys.argv[2]]; print("1" if value is True else "0" if value is False else value)' "$PLAN_FILE" "$1"; }
  TLS_MODE="$(plan_get tls_mode)"
  PUBLIC_URL="$(plan_get public_url)"
  NEED_REDIRECT="$(plan_get need_http01_redirect)"
  ACME_PORT="$(plan_get acme_port)"
  HTTP_BUSY="$(plan_get http_busy)"
  HTTPS_BUSY="$(plan_get https_busy)"
  XUI_DETECTED="$(plan_get xui_detected)"
  HTTPS_PORT="$(plan_get https_port)"
}

explain_port_mode() {
  if [[ "$HTTP_BUSY$HTTPS_BUSY" == 00 ]]; then
    printf '80/443 空闲，由 Caddy 自动申请 HTTPS 证书。\n'
    return 0
  fi
  if [[ "$XUI_DETECTED" == 1 ]]; then
    printf '检测到 3x-ui / x-ui 相关文件，且 80 或 443 已被占用。不会停止 3x-ui 或 xray。\n'
  else
    printf '检测到 80 或 443 已被占用，改为兼容模式申请证书，不抢占现有入站。\n'
  fi
  if [[ "$TLS_MODE" == auto ]]; then
    printf '443 仍可用，Caddy 将使用 TLS-ALPN 申请证书；不会再绑定已被占用的 80 端口。\n'
  else
    printf 'HTTPS 将发布在 %s。证书申请完成后可用 %s 访问。\n' "$HTTPS_PORT" "$PUBLIC_URL"
    printf '若希望继续用 443，可在 3x-ui 为该域名添加回落到 127.0.0.1:%s，不必关闭现有入站。\n' "$HTTPS_PORT"
  fi
}

if (( ! CHECK_ONLY )); then
  [[ "$(uname -s)" == Linux ]] || die '服务器部署需在 Linux 执行；本机校验可用 --check，本地观看仍用 start.command。'
fi
if ! command -v python3 >/dev/null; then
  ((INSTALL && ! CHECK_ONLY)) || die '请先安装 Python 3。'
  apt_system
  "${ROOT_CMD[@]}" apt-get update
  "${ROOT_CMD[@]}" apt-get install -y python3
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/taco-deploy.XXXXXX")"
cleanup() { rm -rf -- "$TEMP_DIR"; clear_acme_redirect; }
trap cleanup EXIT
trap 'printf "部署未完成，请查看上方错误；已有数据卷不会被删除。\n" >&2' ERR
(( ! CHECK_ONLY )) || CONFIG_ARGS+=(--non-interactive)
DOMAIN="$(python3 "$PROJECT_DIR/scripts/deploy_config.py" --output "$TEMP_DIR/.env" "${CONFIG_ARGS[@]}")"
[[ -f "$PROJECT_DIR/vendor/hongguo/endpoints/duanju.py" ]] || die '仓库不完整，缺少 vendor/hongguo 运行模块。'

if ! command -v docker >/dev/null; then
  ((INSTALL && ! CHECK_ONLY)) || die '请先安装 Docker Engine 和 Docker Compose 插件。'
  install_docker
fi
docker compose version >/dev/null 2>&1 || die '缺少 Docker Compose 插件，请安装 docker-compose-plugin。'
compose() {
  # Host shell variables must not override the settings that just passed validation.
  # Avoid empty-array expansion: macOS Bash 3.2 nounset treats "${arr[@]}" as unbound.
  if [[ -f "$RUNTIME_ENV" ]]; then
    (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
     "${DOCKER[@]}" compose --project-directory "$PROJECT_DIR" --env-file "$TEMP_DIR/.env" --env-file "$RUNTIME_ENV" -f "$PROJECT_DIR/compose.yaml" "$@")
  else
    (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME TACO_HTTP_PUBLISH TACO_HTTPS_PUBLISH TACO_HTTPS_UDP_PUBLISH TACO_PUBLIC_PORT TACO_CADDYFILE TACO_CERT_DIR HONGGUO_PUBLIC_URL
     "${DOCKER[@]}" compose --project-directory "$PROJECT_DIR" --env-file "$TEMP_DIR/.env" -f "$PROJECT_DIR/compose.yaml" "$@")
  fi
}
compose config --quiet
if ((CHECK_ONLY)); then
  printf '配置检查通过：https://%s（未修改 .env，未启动服务）\n' "$DOMAIN"
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  root_access
  DOCKER=("${ROOT_CMD[@]}" docker)
  "${DOCKER[@]}" info >/dev/null 2>&1 || die 'Docker 服务未启动或当前用户无访问权限。'
fi
compose up --help | grep -- '--wait-timeout' >/dev/null || die 'Docker Compose 版本过旧，请升级到支持 --wait-timeout 的版本。'

plan_https
explain_port_mode
if [[ "$TLS_MODE" == file ]]; then
  issue_certificate "$PROJECT_DIR/deploy/certs" "$NEED_REDIRECT" "$ACME_PORT"
  [[ -f "$PROJECT_DIR/deploy/certs/fullchain.pem" && -f "$PROJECT_DIR/deploy/certs/privkey.pem" ]] || die '缺少 HTTPS 证书文件。'
fi
compose config --quiet

printf '正在构建 TACO小剧场…\n'
compose build --pull --build-arg "GEOIP_MONTH=$(date -u +%Y-%m)" web
compose pull caddy
install -m 600 "$TEMP_DIR/.env" "$PROJECT_DIR/.env"
if ! compose up -d --wait --wait-timeout 180; then
  printf '容器启动或健康检查失败；已有配置和数据卷保留。\n' >&2
  printf '在任意目录查看本项目日志：bash %q --logs\n' "$PROJECT_DIR/deploy.sh" >&2
  exit 1
fi

# Check the public HTTPS endpoint, not just whether container processes exist.
printf '正在等待 %s 的 HTTPS 证书与访问检查…\n' "$PUBLIC_URL"
if ! python3 "$PROJECT_DIR/scripts/check_https.py" "$PUBLIC_URL"; then
  printf '容器已启动，但公网 HTTPS 验证未通过。请检查域名解析、防火墙，以及 3x-ui 是否占用了校验端口。\n' >&2
  printf '查看日志：docker compose logs --tail=80 caddy\n' >&2
  exit 1
fi
printf '\nTACO小剧场已启动：%s\n' "$PUBLIC_URL"
compose exec -T web python scripts/setup_link.py --if-needed
