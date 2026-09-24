#!/usr/bin/env bash
# TACO小剧场: deploy this checkout; no download of a second private repository.
set -Eeuo pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECK_ONLY=0
INSTALL=1
CONFIG_ARGS=(--existing "$PROJECT_DIR/.env")
DOCKER=(docker)
ROOT_CMD=()

usage() {
  cat <<'HELP'
TACO小剧场一键部署（Linux）
  bash deploy.sh                         交互填写域名与线路；已配置时直接更新
  bash deploy.sh --domain video.your-domain.com --direct
  bash deploy.sh --domain video.your-domain.com --proxy socks5://proxy-host:1080
  bash deploy.sh --check                  只校验配置，不安装、构建或启动
  bash deploy.sh --no-install             缺少 Docker/Python 时直接报错

首次使用需将真实域名解析到服务器，并开放 80/TCP、443/TCP。
海外服务器使用 --direct；默认 overseas 需要容器可达的海外代理。
含密码的代理建议交互输入，避免保存到 Shell 历史。
Ubuntu/Debian 缺少 Docker 时通过官方 apt 源安装；其他 Linux 请先安装 Docker Compose。
脚本保留现有账号和数据卷；更新前先在仓库运行 git pull --ff-only。
HELP
}

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --domain|--proxy)
      (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || die "$1 缺少参数"
      CONFIG_ARGS+=("$1" "$2"); shift 2 ;;
    --direct) CONFIG_ARGS+=("$1"); shift ;;
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
trap 'rm -rf -- "$TEMP_DIR"' EXIT
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
  (unset DOMAIN HONGGUO_NETWORK_MODE HONGGUO_UPSTREAM_PROXY COMPOSE_PROJECT_NAME
   "${DOCKER[@]}" compose --project-directory "$PROJECT_DIR" --env-file "$TEMP_DIR/.env" -f "$PROJECT_DIR/compose.yaml" "$@")
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
printf '正在构建 TACO小剧场…\n'
compose build --pull web
compose pull caddy
install -m 600 "$TEMP_DIR/.env" "$PROJECT_DIR/.env"
compose up -d --wait --wait-timeout 180

# Check the public HTTPS endpoint, not just whether container processes exist.
printf '正在等待 https://%s 的 HTTPS 证书与访问检查…\n' "$DOMAIN"
if ! python3 "$PROJECT_DIR/scripts/check_https.py" "https://$DOMAIN"; then
  printf '容器已启动，但公网 HTTPS 验证未通过。请检查域名解析与 80/443 端口。\n' >&2
  printf '查看日志：docker compose logs --tail=80 caddy\n' >&2
  exit 1
fi
printf '\nTACO小剧场已启动：https://%s\n' "$DOMAIN"
compose exec -T web python scripts/setup_link.py --if-needed
