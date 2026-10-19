# 本机源码部署

- 对外地址：https://task.unionclin.com:8090
- 管理入口：https://task.unionclin.com:8090/god-mode/
- 内网直连：http://192.168.0.238:8090（仅排障，见「对外地址与反向代理」）
- Compose 项目：plane-source
- 数据库：用户提供的阿里云 PostgreSQL / project_task（经用户确认使用非 SSL 连接）
- API 配置：apps/api/.env
- 内部服务配置：.deploy/runtime.env、.deploy/live.env、.deploy/relay.env
- 凭据文件权限：600，不应提交到 Git；`.deploy/` 和 `.env` 已在 `.gitignore` / `.dockerignore` 排除。
- 开发分支：`dev/localization`，基于远程默认主分支 `preview`。

## 新机器初始化

需要 Docker Engine 28+（支持 `--gw-priority`）、Docker Compose、Python 3。当前固定端口 8090 和网段 172.24.0.0/16，如冲突请修改 Compose 文件。初始化脚本不会覆盖已有配置或重新生成现有密钥。

```bash
git clone --branch dev/localization https://github.com/bestNiu/plane.git
cd plane
python3 deployments/init-source.py \
  --db-host YOUR_POSTGRES_HOST \
  --db-name YOUR_DATABASE \
  --db-user YOUR_USER \
  --base-url https://task.unionclin.com:8090 \
  --sslmode require
# 交互输入数据库密码，不在命令行传密码。
# 仅在确认允许不加密且数据库不支持 SSL 时使用 --sslmode disable。
```

`deployments/env/*.env.example` 是无真实凭据的模板，由初始化脚本渲染，不要直接拷贝使用。数据库需是专用空库；启动会自动执行 Django 迁移。当前主机已有配置，**不要重新初始化**。

## 启动与维护

```bash
# 源码构建并启动（迁移任务必须成功，API 才会启动）
docker compose -f docker-compose.deploy.yml build --parallel
bash deployments/start-source.sh

# 状态和日志
docker compose -f docker-compose.deploy.yml ps -a
docker compose -f docker-compose.deploy.yml logs --tail=100

# 停止，不删除数据卷
docker compose -f docker-compose.deploy.yml down
```

Redis、RabbitMQ、对象存储均在项目独立网络内，无宿主机端口映射。仅代理映射 8090。

本机新建 Docker 网桥无法直连 RDS，部署额外使用无凭据的 TCP 出站转发容器 `pg-egress`：它连接 Docker 默认网桥和 Plane 私有网桥，以默认网桥为出站网关。API 连接 `pg-egress:15432`，转发目标仍是用户提供的 RDS。转发端口不映射到宿主机。`start-source.sh` 负责双网络连接；停止后重新启动必须使用该脚本。首次访问管理入口完成实例管理员初始化；没有预设管理员密码。线上数据库和 Docker 数据卷均需要定期备份。不要执行 `down -v`，它会删除本地文件存储等持久化数据。

数据库连接仍不加密，正式使用前建议启用 RDS SSL。

## 对外地址与反向代理

TLS 由上游 nginx 终止：`https://task.unionclin.com:8090` → 本机 Caddy `:8090`（HTTP）→ web/admin/space/api/live/minio。因此本机所有服务只跑 HTTP，对外地址必须写全端口 `:8090`。

上游 nginx 需要转发 `X-Forwarded-Proto: https`（已验证），Django 靠它和 `SECURE_PROXY_SSL_HEADER` 判断 `request.is_secure()`。上游转发的 `Host` 不带端口（`Host: task.unionclin.com`），而 MinIO 的预签名 URL 把 Host 计入签名、Django 也用 `request.get_host()` 生成这些 URL，端口一丢就会 403 `SignatureDoesNotMatch`，表现为项目封面、附件、头像、Logo、导出文件全部打不开。本机由 `.deploy/conf.d/public-host.conf`（`request_header Host task.unionclin.com:8090`）在 Caddy 侧补回完整 origin，`apps/proxy/Caddyfile.ce` 通过 `import /etc/caddy/conf.d/*.conf` 加载，无需重建镜像。若能把上游改成 `proxy_set_header Host $http_host;`，该文件可以删除。

内网 `http://192.168.0.238:8090` 和公网 `https://task.unionclin.com:8090` 都可以登录：两个源都写进了 `CORS_ALLOWED_ORIGINS`（API 和 Live 各一份）。Plane 的规则是白名单里只要有一个 `http:` 源，`SESSION_COOKIE_SECURE` 和 `CSRF_COOKIE_SECURE` 就为 False，所以内网 HTTP 也能拿到会话 Cookie；代价是公网侧 Cookie 不再带 Secure 标记。想恢复只允许公网登录，删掉那个 `http:` 源即可，其他不用动。

附件、封面、头像、导出用的是 MinIO 预签名 URL，Host 和协议都算进签名，所以两件事必须同时成立：Caddy 把 Host 统一改写成公网 origin（见上一段），`apps/api/.env` 里 `MINIO_ENDPOINT_SSL=1` 把协议钉死为 https。这样内网 HTTP 访问也会拿到 `https://task.unionclin.com:8090/uploads/...`，内外网都能打开。任一项被关掉，内网就会签出 `http://task.unionclin.com:8090/...`，被公网入口以 400 拒绝。

更换对外地址时同步改这三处，然后 `bash deployments/start-source.sh`：

1. `apps/api/.env`：`WEB_URL`、`APP_BASE_URL`、`ADMIN_BASE_URL`、`SPACE_BASE_URL`、`LIVE_BASE_URL`、`CORS_ALLOWED_ORIGINS`（保留内网源则一并改）
2. `.deploy/live.env`：`CORS_ALLOWED_ORIGINS`（同上）
3. `.deploy/conf.d/public-host.conf`：`request_header Host <新地址的 host:port>`

换内网地址时同步改两处 `CORS_ALLOWED_ORIGINS` 里的 `http://192.168.0.238:8090`。

前端镜像不烧录地址（`VITE_*_BASE_URL` 为空，运行时按同源解析），换地址不需要重建 web/admin/space。邮件里的链接由 `base_host()` 从上述 API 配置生成，同样不需要改代码。

## 邮件配置

Plane 通知/邀请使用 SMTP，不使用 IMAP。网易企业邮箱使用 `smtp.qiye.163.com:465`，启用 SSL（`EMAIL_USE_SSL=1`），关闭 STARTTLS（`EMAIL_USE_TLS=0`）。参考 `deployments/env/mail.env.example`；真实授权码只保存在被忽略的本机配置及 Plane 加密数据库字段中，不提交到 Git。

在 God mode 的邮件设置中配置账号、授权码、服务器、端口和发件人。已有实例默认优先读取数据库配置，仅修改环境变量不会覆盖数据库中的旧值。本机发信实际走 God mode 写入的加密数据库配置，`.deploy/mail.env` 只是留档：`docker-compose.deploy.yml` 并未引用它，`apps/api/.env` 里也没有 `EMAIL_*`，容器内 `EMAIL_HOST` 回落到 `localhost:25`。数据库配置一旦丢失，通知邮件会静默发不出去，届时再把 `mail.env` 挂进 api/worker/beat-worker 的 `env_file`。

本机私有网桥无法直连 SMTP，`start-source.sh` 会为 API、worker、beat-worker 附加 Docker 默认网桥，并将其设为出站网关；内部服务通信仍使用 Plane 私有网络。容器重建后应使用该脚本启动以恢复出站连接。SSL 证书校验保持启用。

## 二开工作流

```bash
git switch dev/localization
git fetch origin
# 合并主分支更新；先提交/暂存本地改动并解决可能的冲突。
git merge origin/preview

# 修改后构建并启动
# 后端变化时启动会执行新增迁移，请提前备份数据库。
docker compose -f docker-compose.deploy.yml build --parallel
bash deployments/start-source.sh

python3 -m unittest discover -s deployments -p 'test_*.py'
git push origin dev/localization
```

本机 GitHub 推送凭据独立保存于 `~/.config/plane-git/credentials`（600，目录 700），Git 认证仅配置在本仓库 `.git/config`，不提交到远程。该凭据为受权限保护的明文文件，应限制主机访问并定期轮换；Token 已出现在聊天中，建议更换。
