# TACO小剧场 · 服务器安装与运维

仓库：[tztmr/video_web_play](https://github.com/tztmr/video_web_play)。网站、API 和播放模块已随仓库提供，容器自动安装 Python 3.11 与 FFmpeg。

## 一键安装

准备 Linux 服务器，将实际域名的 A/AAAA 记录指向服务器。80/443 空闲时开放 80/TCP、443/TCP（443/UDP 可选）。若本机 3x-ui / xray 已占用 80 或 443，不要关闭这些入站；改为放行脚本选出的备用 HTTPS 端口（常见为 8443/TCP）。若配置 AAAA，IPv6 也必须可达。默认配置要求域名直接解析到服务器，使用 Cloudflare DNS 时先关闭 CDN 代理。

在 root 终端执行：

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/tztmr/video_web_play/main/deploy.sh -o taco-install.sh && bash taco-install.sh
```

选择 **1 一键安装**，填写部署目录（默认 `/opt/taco-cinema`）、域名、出口线路。该入口是独立脚本，无需提前克隆仓库。普通账号应使用可写目录，安装系统依赖时需 sudo；默认 `/opt` 目录建议由 root 管理。

Ubuntu/Debian 缺少 Git、Python 3 或 Docker 时自动安装。Docker 使用官方 apt 源；已有冲突容器软件时停止并提示，不自动卸载。其他 Linux 请先安装 Git、Python 3、Docker Engine 和 Compose 插件。

脚本依次校验配置、构建镜像、下载国家 IP 地址库、启动服务、等待容器健康和公网 HTTPS，再打印一次性管理员初始化链接。80/443 空闲时由 Caddy 自动申请证书；与 3x-ui 共存时改为复用已有证书或独立签发，不会停止 3x-ui。没有默认账号或密码。打开初始化链接设置管理员后，该链接立即失效。

## 与 3x-ui / xray 共存

安装脚本检测到 80 或 443 被占用时，**不会**执行 `systemctl stop x-ui`，也不会改写 3x-ui 的 acme.sh `reloadcmd`。证书工具安装在项目目录 `deploy/acme/`，与 `/root/.acme.sh` 分开。

| 端口情况 | HTTPS 行为 |
| --- | --- |
| 80、443 都空闲 | 仍由 Caddy 自动申请证书，公网地址 `https://域名` |
| 仅 80 被占用，443 空闲 | Caddy 使用 TLS-ALPN 在 443 申请证书，不再绑定主机 80 |
| 443 被占用 | 复用 3x-ui / acme.sh / `deploy/certs` 中尚未过期的证书；没有可用证书时才临时借用 80 做 HTTP-01。网站发布在空闲端口（通常是 `https://域名:8443`） |

请把浏览器、分享链接和管理初始化入口写成脚本打印的公网地址。若必须继续用 443 访问网站，可在 3x-ui 为该域名增加回落到 `127.0.0.1:8443`（或脚本提示的端口），不必关闭现有入站。复用 3x-ui 证书后，对方续期不会自动同步；把新的 `fullchain.pem` / `privkey.pem` 放到 `deploy/certs/` 后重新部署，或再执行一次安装/更新。防火墙需放行实际使用的 HTTPS 端口。

## 中文运维菜单

以后运行 `bash taco-install.sh`，或安装目录内的 `bash deploy.sh`。部署目录保存在当前用户的 `~/.local/state/taco-cinema/install-dir`，不是可执行 Shell 配置。请使用部署时相同账号；也可传 `--directory` 指定目录。

| 菜单 | 操作 |
| --- | --- |
| 1 | 一键安装 / 继续部署，保留已有账号和数据 |
| 2 | 获取 GitHub main，快进更新并重新部署 |
| 3 | 修改域名、HTTPS 和出口线路，空值保留原设置 |
| 4 / 5 | 查看服务状态 / 最近日志 |
| 6 / 7 / 8 | 重启 / 停止 / 启动服务 |
| 9 | 在线备份账号、分享、设备配置与部署配置 |
| 10 / 11 | 管理员初始化链接 / 比较源码版本 |
| 12 | 确认后移除本项目容器和网络，保留代码与数据卷 |
| 13 | 构建当月地址库并重新部署 |

更新会检查仓库地址、main 分支和工作区改动。遇到未提交文件、分支不符或无法快进时停止，不强制覆盖。地址库或镜像构建失败不会继续启动新版本。配置通过校验和构建后才写回 `.env`；已运行的容器更新失败时请先查日志再重试。

也支持命令行：

```bash
bash taco-install.sh --install --directory /opt/taco-cinema --domain video.your-domain.com --direct
bash taco-install.sh --update
bash taco-install.sh --configure
bash taco-install.sh --status
bash taco-install.sh --logs
bash taco-install.sh --restart
bash taco-install.sh --backup
bash taco-install.sh --setup-link
bash taco-install.sh --refresh-geoip
bash taco-install.sh --uninstall
```

`--help` 查看完整用法。`--no-install` 禁止安装系统依赖。`--check` 只检查已有部署配置，不修改 `.env`、不构建、不启动，也不验证 DNS 或代理可达性；首次检查可传 `--check --domain 实际域名 --direct`。

## 默认屏蔽大陆 IP

所有网站网页、登录、管理、API 与服务器视频接口统一检查来源 IP，浏览器获取直连播放地址的接口也在其中。地址库国家代码 `CN` 返回 HTTP 403；港澳台不在屏蔽范围内。有效账号和免登录分享链接都不能跳过检查。IPv4、IPv6 和 IPv4 映射地址均检查。视频源站 CDN 不受本网站控制，已经取得的播放地址和内容无法远程收回。

使用本地 [DB-IP Lite 国家地址库](https://db-ip.com/db/download/ip-to-country-lite)，每次请求不调用外部定位 API。数据库无法加载或无法判断地区时返回 503，不自动放行。生产环境健康检查也要求地址库可用。本机回环地址 `127.0.0.1` / `::1` 保留本地访问和容器健康检查。

判断的是**公网出口 IP**；使用海外代理的访客显示为代理地区，不能据此判断实际所在地。Lite 数据也可能有误差。数据每月更新，建议每月执行菜单 13；当月尚未发布时使用上月数据。镜像内 `/opt/geoip/country.json` 记录版本和校验值。来源署名及许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。

公开服务只经过 Caddy，web 的 8787 端口不映射到宿主机。Caddy 用 TCP 来源重写 `X-Forwarded-For`，删除客户端提交的其他常见 IP 头；应用不直接采信 `CF-Connecting-IP`、`X-Real-IP`。`FORWARDED_ALLOW_IPS=*` 仅用于这条隔离的内部代理链。**不要直接公开 8787，也不要在此配置前直接套 CDN / 其他反向代理**；否则看到的是中间服务器出口，需另行配置并验证受信任代理的真实访客 IP 链。

本地开发可运行 `.venv/bin/python scripts/update_geoip.py` 更新 `.data/geoip-country.mmdb`，重启后生效。Docker 部署由构建过程自动完成。

## 上游出口线路

入站地区限制与取片出口是两件事。默认 `overseas` 需要容器可以访问的海外代理，缺失时不自动直连。代理含密码时建议交互输入，避免保存到 Shell 历史。

海外服务器可选择「2 服务器已在海外」，或显式使用 `--direct`。无凭据代理示例：

```bash
bash taco-install.sh --install --domain video.your-domain.com --proxy socks5://proxy.your-domain.com:1080
```

将示例地址替换为实际地址。容器中的 `127.0.0.1` / `localhost` 是容器自身，不能作为宿主机代理。服务器需要自己的出口配置；脚本不复制本机 v2rayN 凭据。代理影响后端 API 和兼容模式视频请求；默认直连视频和封面使用观众设备自己的网络/代理规则。

## 美国服务器如何减少视频流量

安装时选择「服务器已在海外」（`--direct`），表示后端 API 使用美国主机出口。播放器默认选择「源站直连」，表示视频由**观众浏览器 → 视频源站 CDN**获取。两种“直连”分别控制后端出口和视频传输路径。

源站直连只向网站索取少量播放信息，视频由浏览器 Worker 下载和解密，不在服务器下载、转码或生成缓存，也不触发原来的服务器下一集预下载。登录、目录、网页等请求仍经过服务器。

直连要求网站 HTTPS、源站允许跨域，以及观众设备支持该集的 H.264/HEVC 编码。当前先完整取回单集再播放，单集上限 256 MiB；上游只有 ByteVC2、设备不支持 HEVC、网络或跨域失败时，界面提示手动选择「兼容模式」。**不会自动回退**；兼容模式仍经美国服务器传输视频并消耗流量。更新后刷新网页即可使用，无需迁移账号、分享或旧缓存。

分享接口在签发播放信息前后检查链接范围和有效期；过期或撤销后不再返回新地址，分享页定期检查并停止播放。已经发给浏览器的地址、密钥或视频不能收回，网站的地区限制也不能控制上游 CDN 对这些地址的响应。

已有安装在原目录执行 `bash deploy.sh --update`（或菜单 2），更新完成后刷新播放器。服务器侧无需开放额外端口。

## 配置与数据

`.env` 支持 `DOMAIN`、`HONGGUO_NETWORK_MODE`、`HONGGUO_UPSTREAM_PROXY`、`COMPOSE_PROJECT_NAME`，以 600 权限保存，不作为 Shell 代码执行。代理值不主动打印。

新项目名为 `taco-cinema`。旧版 `.env` 中的 `HONGGUO_SOURCE_DIR` 用于识别 `hongguo-cinema` 数据卷迁移；已有其他项目名时指定 `COMPOSE_PROJECT_NAME='原项目名'`，避免产生另一组空数据卷。底层 `HONGGUO_*` 变量、Cookie 和浏览器存储键保持兼容。

- `cinema_data`：账号、会话、分享、设备池与缓存。
- `caddy_data` / `caddy_config`：HTTPS 证书和代理状态。
- 菜单备份使用 SQLite 在线备份 API，包含已提交的 WAL 数据；排除视频缓存，不需要停机。归档保存在部署目录 `backups/`，目录权限 700、文件 600，不提交 Git。
- 备份含 `data.tar`（数据库、设备池及尚未使用的初始化令牌）、`site.env`、`REVISION`，可能包含敏感配置，请保存在私有位置。
- 恢复时先停止 web，将 `data.tar` 中的文件恢复至 `cinema_data` 卷根目录，移除旧数据库的 `-wal` / `-shm` 残留，并设置 UID/GID 为 `10001:10001`；将 `site.env` 恢复为 `.env`（600）。确认项目名和域名后启动服务。建议先在独立实例验证恢复。
- 卸载不删除卷，重新安装可以继续使用原数据；脚本不会运行全局 Docker 清理。

## 故障与验证

若日志出现 `python: can't open file '/app/server.py': [Errno 13] Permission denied`，是旧镜像继承了安装脚本 `umask 077` 产生的源码权限，普通容器用户无法读取。新镜像会在内部修正程序文件的读取权限，并在构建阶段以运行用户检查导入与国家库；宿主机 `.env`、备份和数据卷权限不变。运行原来的 `bash taco-install.sh --update`（无需进入安装目录），或进入项目目录执行 `bash deploy.sh --update`，重新构建即可，不需要删除卷或重新初始化账号。

如果停留在 `/root` 等非项目目录，`docker compose logs` 会提示找不到配置。使用原安装脚本的 `--logs`（读取已保存的安装目录），或默认项目名下直接运行 `docker logs --tail=80 taco-cinema-web-1`。启动失败时新部署脚本也会显示带绝对路径的日志命令。

容器启动但 HTTPS 失败时，检查域名解析、80/443 或备用 HTTPS 端口（常见 8443）、错误 AAAA、3x-ui 是否占用了校验端口，以及 Caddy 日志。修正后再次执行安装或更新。如果 HTTPS 检查请求的出口为大陆 IP，脚本会确认正确的地区拒绝响应并明确提示；这不表示大陆浏览器能够播放。

当前使用单 web 进程，不支持直接增加多 worker。兼容模式只有一个视频准备名额，同一集共享下载和转换，其承载能力取决于服务器带宽、磁盘和码率；直连模式不占用该名额，播放速度取决于观众到源站的网络和设备解码能力。

GitHub Actions 检查 Python 用例、前端和 Shell 语法、Compose 配置，使用 `umask 077` 克隆出的真实受限权限目录构建 Linux 镜像，验证 UID 10001 启动、静态播放模块、数据卷重启保留、登录、真实国家库、IPv4/IPv6 拦截与 Caddy 请求头防伪。结果以仓库 Actions 实际运行状态为准。

尚未连接目标美国服务器或域名验收。上线后用真实大陆网络确认网站返回 403，用海外网络检查初始化、登录、匿名分享、撤销、直连播放、拖动和切集；浏览器网络记录应看到源站视频请求以及小体积 `/source` 响应，不应出现 `/stream`、`/media/` 或 `/prefetch` 请求。再手动切换兼容模式检查服务器传输。单机代理或模拟来源地址不能代替真实地区客户端验收。
