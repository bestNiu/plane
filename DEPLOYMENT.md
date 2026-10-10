# 本机源码部署

- 地址：http://192.168.0.238:8090
- 管理入口：http://192.168.0.238:8090/god-mode/
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
  --base-url http://192.168.0.238:8090 \
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

当前使用 HTTP，且数据库连接不加密；正式公网使用前建议配置 HTTPS 并启用 RDS SSL。更换访问地址时需要同步更新 API 的 URL/CORS 配置以及 Live CORS 配置。

## 邮件配置

Plane 通知/邀请使用 SMTP，不使用 IMAP。网易企业邮箱使用 `smtp.qiye.163.com:465`，启用 SSL（`EMAIL_USE_SSL=1`），关闭 STARTTLS（`EMAIL_USE_TLS=0`）。参考 `deployments/env/mail.env.example`；真实授权码只保存在被忽略的本机配置及 Plane 加密数据库字段中，不提交到 Git。

在 God mode 的邮件设置中配置账号、授权码、服务器、端口和发件人。已有实例默认优先读取数据库配置，仅修改环境变量不会覆盖数据库中的旧值。本机 `.deploy/mail.env` 保存邮件参数，同时已同步至 `apps/api/.env` 和 Plane 实例配置。

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

