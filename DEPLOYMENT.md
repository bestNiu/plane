# 本机源码部署

- 对外地址：https://task.unionclin.com:8090
- 管理入口：https://task.unionclin.com:8090/god-mode/
- 内网直连：http://192.168.0.238:8090（仅排障，见「对外地址与反向代理」）
- Compose 项目：plane-source
- 数据库：用户提供的阿里云 PostgreSQL / project_task（经用户确认使用非 SSL 连接）
- API 配置：apps/api/.env
- 内部服务配置：.deploy/runtime.env、.deploy/live.env、.deploy/relay.env
- 邮件通知链路与排障：见 [docs/email-notifications.md](docs/email-notifications.md)
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

## 服务与网络拓扑

### 服务清单

| 服务                      | 来源                     | 作用                                                                            | 暴露                          |
| ------------------------- | ------------------------ | ------------------------------------------------------------------------------- | ----------------------------- |
| `proxy`                   | `apps/proxy`             | Caddy，唯一入口，反代到各前端/API/MinIO                                         | **8090:80**（唯一宿主机端口） |
| `web` / `admin` / `space` | 源码构建                 | 主应用、God mode 管理端、公开分享页                                             | 仅经 proxy                    |
| `api`                     | `plane-source-api:local` | Django，gunicorn WSGI + gthread，:8000                                          | 仅内网                        |
| `worker`                  | 同上                     | Celery worker（`--concurrency=2`），跑活动记录与通知任务                        | 仅内网                        |
| `beat-worker`             | 同上                     | Celery beat，每 5 分钟发邮件、每日归档/清理                                     | 仅内网                        |
| `migrator`                | 同上                     | 一次性迁移（`restart: "no"`），失败会阻塞 `api`                                 | 不常驻                        |
| `live`                    | `apps/live`              | 实时协作（WebSocket），与 Django 分离                                           | 仅经 proxy                    |
| `plane-redis`             | valkey 7.2               | 缓存、Celery 结果、发信去重锁、通知 origin 缓存                                 | 仅内网                        |
| `plane-mq`                | rabbitmq 3.13            | Celery broker                                                                   | 仅内网                        |
| `plane-minio`             | silo（S3 兼容）          | 附件/头像/导出，卷 `uploads`                                                    | 仅经 proxy                    |
| `pg-egress`               | python:3.12-slim         | 无凭据 TCP 转发：`15432` → RDS `5432`                                           | 仅内网                        |
| `smtp-egress`             | python:3.12-slim         | 无凭据 TCP 转发：`587` → `smtp.qiye.163.com:587`，共享 `pg-egress` 网络命名空间 | 仅内网                        |

### 网络

- `plane-source_default`（172.24.0.0/16）：服务之间唯一的通信网络，**没有外发路由**。
- Docker 默认网桥 docker0：本机**唯一**能出外网的口子。`pg-egress` 同时挂两条网络，并以 docker0 为默认网关；`smtp-egress` 共享它的命名空间，因此既能出网又能被 Plane 网络访问。
- 因此 `pg-egress` 是**数据库和邮件共同的前提**：它一旦被重建而丢掉 Plane 网络，DB 与邮件会同时不可用。`start-source.sh` 负责补这次网络连接，容器重建后请用它启动（不要只跑 `docker compose up -d`）。

### 数据卷

`redisdata`（Redis/RabbitMQ 之外的缓存数据）、`rabbitmq_data`、`uploads`（MinIO 文件）、`caddy_data` / `caddy_config`。`uploads` 是唯一业务数据，务必纳入备份；**不要执行 `down -v`**。

### 数据库连接复用

API 以 **WSGI + gthread** 提供服务（`plane.wsgi:application`，`-k gthread --threads 20`），不是 ASGI。Django 的 ASGI handler 对**每个请求**单独进入一次 `ThreadSensitiveContext`，同步视图因此每请求都跑在一个全新线程里；Django 的数据库连接是线程本地的，于是每个请求都会新建一条 PostgreSQL 连接，`CONN_MAX_AGE` 永远不生效。改成 gthread 后线程池长期存活，连接才真正被复用。本项目没有 async 视图、也没有 websocket consumer（`plane.asgi` 只路由 `http`，实时协作由独立的 `live` 服务承担），因此切到 WSGI 不损失功能。

相关参数在 `apps/api/.env`：`DB_CONN_MAX_AGE=300`（连接复用窗口，Django 默认为 0）、`DB_CONN_HEALTH_CHECKS=1`（复用前探测，服务端已断开的连接自动重建）、`GUNICORN_THREADS=20`。`DB_CONN_MAX_AGE` 必须保持小于 RDS 的空闲超时。注意 `dj_database_url` **不会**从 `DATABASE_URL` 查询串里读取这两个参数——写在那里会落进 `OPTIONS` 并被 libpq 拒绝，只能作为参数传入，见 `plane/settings/common.py`。

`manage.py wait_for_db` 原先只取 `connections["default"]` 这个惰性包装对象，它恒为真值且从不抛异常，所以命令会在**没有真正连过数据库**的情况下就报告 `Database available!`。现在它执行 `SELECT 1` 实际探测，并在失败时重试（默认 30 次、间隔 2 秒）。这一点很关键：`pg-egress` 到 RDS 的链路本身存在间歇性连接失败，`migrator` 是 `restart: "no"` 的一次性服务，它一旦失败 `api` 就不会启动，整个部署被卡住。

实测效果（`api` 侧 HTTP 500 率，同一令牌）：稳态请求由约 10–21% 降到 0%（连续 140 次请求 0 失败）；30 并发突发由 19/30 失败降到 0/30；每个请求新建的 PG 连接数由约 1.0 降到约 0.1（65 次请求期间新建 7 条，此前为 40 条），空闲时并发后端连接数由 13 降到 4。

残余风险：`pg-egress` 转发链路在高并发新建连接时仍会间歇失败（顺序建连实测约 9.6% 失败率，直连 RDS 约 3.2%）。本次改动通过大幅减少建连次数来降低暴露面，并未修复转发链路本身。彻底解决需要让 Plane 网络直连 RDS 从而移除 `pg-egress`，或在其位置改用一个真正的连接池（如 pgbouncer）。

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

Plane 的通知/邀请使用 SMTP 发信，不使用 IMAP。完整的链路说明、部署步骤、验证命令和排障清单见 [docs/email-notifications.md](docs/email-notifications.md)，这里只记录本机的结论性配置。

本机的两个网络约束决定了配置：

1. **465 被封**：本机到 `smtp.qiye.163.com` 只有 587 / 25 通，465 一律超时。因此必须 **587 + STARTTLS**（`EMAIL_USE_TLS=1`、`EMAIL_USE_SSL=0`），不能用 SSL 直连。
2. **Plane 网络没有出网**：`api` / `worker` 无法直连任何公网 SMTP，必须经 `smtp-egress` 中继。

God mode → 邮件设置（即数据库 `InstanceConfiguration`，本机 `SKIP_ENV_VAR=1` 时**优先且仅**读这里）：

| key                              | 值                                                                            |
| -------------------------------- | ----------------------------------------------------------------------------- |
| `EMAIL_HOST`                     | `smtp.qiye.163.com`（必须是真实主机名，写中继地址会导致 STARTTLS 证书不匹配） |
| `EMAIL_PORT`                     | `1587`（指向中继）                                                            |
| `EMAIL_USE_TLS`                  | `1`                                                                           |
| `EMAIL_USE_SSL`                  | `0`                                                                           |
| `EMAIL_FROM` / `EMAIL_HOST_USER` | `notify@unionclin.com`，授权码填 `EMAIL_HOST_PASSWORD`（加密存储）            |

中继与证书的配合方式：`smtp-egress` 是纯 TCP 转发（TLS 直通），`x-api.extra_hosts` 把 `smtp.qiye.163.com` 钉到 `pg-egress` 在 Plane 网络上的 IP（当前 `172.24.0.4`），所以证书校验针对的仍是真实主机名。若 `pg-egress` 重建后 IP 变化，需同步修改 `docker-compose.deploy.yml` 里 `SMTP_RELAY_LISTEN_HOST` 与 `extra_hosts` 两处。

`.deploy/mail.env` 只是凭据留档：`docker-compose.deploy.yml` 并未引用它，`apps/api/.env` 里也没有 `EMAIL_*`；容器内的邮件配置全部来自数据库。数据库里的邮件配置一旦丢失，通知会静默发不出去，届时可把 `mail.env` 挂进 api/worker/beat-worker 的 `env_file` 兜底。

投递语义（相对上游已改动）：失败不再永久丢弃，`EmailNotificationLog.retry_count` 记录尝试次数，每轮 beat（5 分钟）重试，**最多 3 次**；工作项或用户已删除的批次直接丢弃；发送成功才写 `sent_at`。补发历史邮件的命令见 `docs/email-notifications.md` 第 5.1 节。

`start-source.sh` 仍会为 `api` / `worker` / `beat-worker` 附加 Docker 默认网桥以提供通用出网能力，但 SMTP 不依赖它——邮件固定走 `smtp-egress`，这样即使容器被 `docker compose up` 重建、那次附加丢失，邮件也不会跟着断。

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
