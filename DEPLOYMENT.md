# TACO小剧场 · 服务器部署

本仓库已包含网站、API 与播放所需 Python 模块，不需要再克隆「红果视频下载」。容器自动安装 Python 3.11 与 FFmpeg。

## 一键部署

准备 Linux 服务器，将实际域名的 A/AAAA 记录指向服务器，开放 80/TCP 和 443/TCP；443/UDP 可选。若配置了 AAAA，IPv6 也必须能到达该服务器。

仓库为私有仓库，服务器需有你自己的 GitHub 访问权限。以已有 SSH 权限为例：

```bash
git clone git@github.com:tztmr/video_web_play.git
cd video_web_play
bash deploy.sh
```

按提示填写域名和出口线路即可。Ubuntu/Debian 可自动安装缺少的 Python 3 和 Docker Engine，使用 Docker 官方 apt 源，不执行远程安装脚本。已有 Docker 的其他 Linux 系统也可运行，需安装 Compose 插件。安装依赖需要 root 或 sudo 权限。

脚本校验配置、构建镜像、启动容器并等待健康检查。Caddy 自动申请 HTTPS 证书；脚本对域名进行带证书校验的 HTTPS 请求，通过后在终端显示一次性管理员初始化链接。

打开链接，自行设置管理员账号与密码；没有默认账号或密码。链接完成初始化后立即失效。重跑脚本会保留账号、分享链接和数据卷，不重置管理员。

## 线路选择

默认 `overseas` 需要容器可以访问的海外代理，不会静默回退直连。代理含密码时建议交互输入，避免出现在 Shell 历史中。

海外服务器可显式使用其出口：

```bash
bash deploy.sh --domain video.your-domain.com --direct
```

无凭据代理示例：

```bash
bash deploy.sh --domain video.your-domain.com --proxy socks5://proxy.your-domain.com:1080
```

将示例域名替换为实际地址。容器内的 `127.0.0.1`、`localhost` 指向容器自身，脚本会拒绝将其作为海外代理地址。服务器需要自己的出站方式，不复制本机 v2rayN 配置。后端代理影响 API 和取片请求，封面图片仍使用观众浏览器的网络。

## 更新与检查

```bash
git pull --ff-only
bash deploy.sh
```

重跑使用已有 `.env`，可通过 `--domain`、`--direct` 或 `--proxy` 更新配置。脚本不删除数据卷，不运行 `down -v`。

```bash
# 只检查配置，不修改 .env、不构建、不启动，可在 macOS 执行
bash deploy.sh --check

# 不允许脚本安装系统依赖
bash deploy.sh --no-install

# 状态与日志，需要与部署时相同的 Docker 权限
docker compose ps
docker compose logs --tail=80 web caddy

# 重新显示尚未使用的初始化链接
docker compose exec web python scripts/setup_link.py --if-needed
```

第一次检查尚无 `.env` 时，可传 `--check --domain 你的实际域名 --direct`。检查模式不验证 DNS、代理可达性或证书签发。

新部署 Compose 项目名为 `taco-cinema`。旧版 `hongguo-cinema` 升级时保留旧 `.env` 中的 `HONGGUO_SOURCE_DIR`；脚本据此沿用原项目名及数据卷，再改用仓库内模块。其他项目名在 `.env` 添加 `COMPOSE_PROJECT_NAME='原项目名'`。不要无意修改项目名，否则 Compose 会创建另一组数据卷。

`.env` 支持 `DOMAIN`、`HONGGUO_NETWORK_MODE`、`HONGGUO_UPSTREAM_PROXY` 和 `COMPOSE_PROJECT_NAME`；旧版 `HONGGUO_SOURCE_DIR` 仅用于迁移识别。配置不会作为 Shell 代码执行，含密码的代理值不会由脚本打印。底层 `HONGGUO_*` 环境变量名、Cookie 和浏览器存储键保持兼容。

## HTTPS 与故障定位

8787 端口仅暴露在 Compose 网络，Caddy 对外提供 HTTPS。默认登录后观看；有效分享链接可免登录访问指定短剧，默认 0 天永久有效。

容器已运行但 HTTPS 检查失败时，查看 Caddy 日志并检查域名解析、80/443 端口、错误的 AAAA 记录和端口占用。脚本此时会报失败；修正后可再次执行。

应用校验 Host、Origin 和分享有效期，使用 Secure/HttpOnly/SameSite Cookie。`FORWARDED_ALLOW_IPS=*` 仅适用于现有“web 端口未发布，只经 Caddy 访问”的网络配置。不要设置绕过鉴权的静态视频目录。

Docker 安装参考 [Ubuntu 官方说明](https://docs.docker.com/engine/install/ubuntu/) 和 [Debian 官方说明](https://docs.docker.com/engine/install/debian/)；等待就绪使用 [Compose up 的 --wait](https://docs.docker.com/reference/cli/docker/compose/up/)。

## 数据与容量

- `cinema_data`：账号、会话、分享、设备池、诊断及缓存。备份时停止 web，再备份该卷，恢复时保留权限。
- `caddy_data` / `caddy_config`：HTTPS 证书与代理数据。
- 当前一个 web 进程、一个视频准备名额，同集复用下载和转换，最多预加载下一集。不要直接开启多 worker，任务与缓存尚未做跨进程协调。
- 首次播放仍需下载完整一集并解密，之后在兼容转换期间流式输出；缓存命中速度不能代表首次播放速度。
- 已完成文件可供多位观众读取，并发能力受服务器带宽、磁盘及码率限制。

## 验证边界

GitHub Actions 检查 Python 用例、前端与 Shell 语法、Compose 配置，并在 Linux 构建、启动镜像检查健康与登录边界。最新结果查看仓库 Actions；本地记录见 [VALIDATION.md](VALIDATION.md)。

尚未提供实际服务器或域名，本次不代表完成公网部署、证书签发或大陆/海外观看验收。上线后应从真实大陆和海外网络各测试同一集，区分首次准备与缓存命中，每组至少 3 次，连续播放 60–120 秒，检查拖动、切集、匿名分享和撤销。单机代理出口测试不能替代真实地区客户端验收。
