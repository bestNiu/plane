# 邮件通知部署与运维说明

本文记录本部署（`docker-compose.deploy.yml`）中工作项邮件通知的实现链路、部署步骤、验证方式和已知注意事项。

## 1. 链路总览

```
创建/更新工作项
   └─> plane/api/views/issue.py 或 app/views/issue/base.py
        issue_activity.delay(type=..., notification=True, origin=...)
        └─> bgtasks/issue_activities_task.py  记录 IssueActivity + 把 assignee 写成 IssueSubscriber
             └─> bgtasks/notification_task.py  写站内 Notification + EmailNotificationLog（按用户偏好）
                  └─> bgtasks/email_notification_task.py   Celery beat 每 5 分钟
                        stack_email_notification  按「收件人 + 工作项」聚合成一封信
                        └─> send_email_notification   SMTP 发送，成功写 sent_at
```

- 站内通知**不受**偏好开关影响；邮件受 5 个开关控制（`property_change` / `state_change` / `issue_completed` / `comment` / `mention`，默认全开）。
- 自己指派给自己不会产生通知（actor 被排除）。
- 同一收件人在同一工作项上的多次变更，5 分钟窗口内合并为一封邮件。

## 2. 本部署的特殊之处：SMTP 必须走中继

本机两条网络限制决定了拓扑：

1. **外发端口**：本机到邮件服务商（`smtp.qiye.163.com`）的 **465 被封**，只有 **587 / 25** 通。因此 `EMAIL_USE_SSL=1` 不可用，必须用 587 + STARTTLS（`EMAIL_USE_TLS=1`、`EMAIL_USE_SSL=0`）。
2. **容器出网**：`plane-source_default`（172.24.0.0/16）**完全没有外发路由**，只有 docker0 有。因此 api/worker 无法直连任何公网 SMTP。

解决办法：`smtp-egress` 中继容器

```yaml
smtp-egress:
  image: python:3.12-slim
  network_mode: "service:pg-egress" # 共享 pg-egress 的网络命名空间
  command: python /relay.py
  volumes:
    - ./deployments/smtp-egress.py:/relay.py:ro
  environment:
    SMTP_RELAY_HOST: smtp.qiye.163.com
    SMTP_RELAY_PORT: 587
    SMTP_RELAY_LISTEN_HOST: 172.24.0.4 # pg-egress 在 plane 网络上的 IP
    SMTP_RELAY_LISTEN_PORT: 1587
  depends_on:
    - pg-egress
```

- `pg-egress` 是唯一同时「在 plane 网络内」且「有出网」的容器，共享其 netns 即可两头通。
- 中继是纯 TCP 转发，TLS 直通，因此**证书校验仍然针对真实主机名**。这就是为什么 `x-api.extra_hosts` 要把真名钉到中继 IP，而不是把 `EMAIL_HOST` 写成中继地址：

```yaml
x-api: &api
  extra_hosts:
    - "smtp.qiye.163.com:172.24.0.4"
```

## 3. 部署步骤

```bash
# 1) 拉起中继（pg-egress 会先被检查/启动）
docker compose -f docker-compose.deploy.yml up -d smtp-egress

# 2) 重建 api/worker/beat-worker，让 extra_hosts 与代码生效
docker compose -f docker-compose.deploy.yml build api
docker compose -f docker-compose.deploy.yml up -d --no-deps api worker beat-worker

# 3) 应用迁移（新增 email_notification_logs.retry_count）
docker exec plane-source-api-1 python manage.py migrate
```

实例配置（Admin 后台 → Instance Settings，或直接改库）：

| key             | value                  | 说明                             |
| --------------- | ---------------------- | -------------------------------- |
| `EMAIL_HOST`    | `smtp.qiye.163.com`    | 必须是真实主机名，不能写中继地址 |
| `EMAIL_PORT`    | `1587`                 | 指向中继端口                     |
| `EMAIL_USE_TLS` | `1`                    | 587 + STARTTLS                   |
| `EMAIL_USE_SSL` | `0`                    | 465 被封，不能用 SSL             |
| `EMAIL_FROM`    | `notify@unionclin.com` | 需与 `EMAIL_HOST_USER` 同域      |
| `ENABLE_SMTP`   | `1`                    |                                  |

> 注意：本部署 `SKIP_ENV_VAR=1`（默认值），所以邮件配置以**数据库里的 InstanceConfiguration 为准**，`apps/api/.env` 里的 `EMAIL_*` 不生效。

## 4. 验证

```bash
# 4.1 中继到服务商是否通（应看到 220 欢迎语）
docker exec plane-source-api-1 python -c "
import socket
s=socket.create_connection(('smtp.qiye.163.com',1587),timeout=10)
print(s.recv(120).strip()); s.close()"

# 4.2 用应用自己的配置发一封测试信
docker exec plane-source-worker-1 python manage.py shell -c "
from plane.license.utils.instance_value import get_email_configuration
from django.core.mail import get_connection, EmailMultiAlternatives
h,u,p,port,tls,ssl,frm = get_email_configuration()
c = get_connection(host=h, port=int(port), username=u, password=p, use_tls=tls=='1', use_ssl=ssl=='1')
EmailMultiAlternatives('SMTP 测试','ok',frm,['you@example.com'],connection=c).send(); print('OK')"

# 4.3 看投递状态（sent_at 为空说明还没发成功）
docker exec plane-source-api-1 python manage.py shell -c "
from plane.db.models import EmailNotificationLog
print('sent:', EmailNotificationLog.objects.filter(sent_at__isnull=False).count(),
      'pending:', EmailNotificationLog.objects.filter(sent_at__isnull=True).count())"

# 4.4 看 worker 日志
docker logs plane-source-worker-1 --since 10m 2>&1 | grep -E 'Email Sent Successfully|plane.exception'
```

## 5. 注意事项

### 5.1 重试语义

- 发送成功才写 `sent_at`；失败保留 `processed_at` 与 `retry_count`，**下一轮 beat（5 分钟后）自动重试**。
- 最多重试 `MAX_EMAIL_RETRIES = 3` 次，之后不再尝试（避免 SMTP 挂掉时无限堆积）。
- 工作项或收件人已删除的批次会直接丢弃，不再占用重试次数。
- 想手动补发历史邮件（例如 SMTP 长时间不通后恢复）：

```bash
docker exec plane-source-api-1 python manage.py shell -c "
from plane.db.models import EmailNotificationLog
print(EmailNotificationLog.objects.filter(sent_at__isnull=True).update(processed_at=None, retry_count=0))"
```

下一轮 beat 会重发；SMTP 发送量大时注意服务商的频率限制。

### 5.2 pg-egress 是硬依赖

`smtp-egress` 依赖 `pg-egress` 同时具备两个网络。`pg-egress` 在 compose 里是 `network_mode: bridge`，**手工**挂到 plane 网络的：

```bash
docker network connect plane-source_default plane-source-pg-egress-1 --alias pg-egress
```

如果 `docker compose up` 重建了 `pg-egress`，这条连接会丢失 —— 后果是**数据库和邮件同时不可用**，需重新执行上面的命令。

### 5.3 端口与命名

- 中继监听 `172.24.0.4:1587`（`pg-egress` 的 plane 网 IP + 非标准端口），刻意避开 587，避免与将来在宿主上跑真实 MTA 冲突。
- `172.24.0.4` 由 compose 的 IPAM 分配，若 `pg-egress` 重建后 IP 变了，需要同步修改 `smtp-egress` 的 `SMTP_RELAY_LISTEN_HOST` 与 `x-api.extra_hosts`。

### 5.4 不能用的写法（踩过的坑）

| 写法                                                 | 结果                                                                            |
| ---------------------------------------------------- | ------------------------------------------------------------------------------- |
| `EMAIL_PORT=465` + `EMAIL_USE_SSL=1`                 | 出网被封锁，`TimeoutError [Errno 110]`，日志里能看到堆栈                        |
| `EMAIL_HOST=<中继容器名>`                            | STARTTLS 报 `Hostname mismatch`，证书不匹配                                     |
| 中继用 `network_mode: host`，容器连 `172.24.0.1:587` | 宿主防火墙丢弃容器到宿主的入站，超时                                            |
| 中继新建 user bridge 网络                            | 该网段没有出网，中继自己连不上服务商                                            |
| compose 里 `networks: {external: bridge}`            | docker 报 `network-scoped aliases are only supported for user-defined networks` |

### 5.5 代码侧行为变化（相对上游）

- `EmailNotificationLog.retry_count`：新增字段（迁移 `0123`）。
- 日志归属改为**按工作项**认领：修复了「同一收件人有多个工作项时，第一封发成功就把其余日志全部标为已发」的问题。
- `get_base_api()`：redis 中的 origin 只保存 10 分钟，超时后回退到 `WEB_URL`，不再静默丢弃邮件。
- `notification_task.get_notification_preference()`：偏好行缺失时自动补建（默认全开），不再因 `DoesNotExist` 让整批通知任务中断。
- 外部 API（`/api/v1`）创建工作项时传 `assignees`，之前不会记录指派活动、被指派人也不会成为订阅者（因此收不到任何通知）；现在 `assignees` 与 `assignee_ids` 同等处理。

## 6. 排障清单

| 现象                                | 排查方向                                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------------------------ |
| 完全收不到邮件                      | `EMAIL_HOST` 是否被改回 465/中继名；`smtp-egress` 容器是否 Up；`pg-egress` 是否还挂在 plane 网络 |
| `Hostname mismatch`                 | `extra_hosts` 是否生效（`docker exec api grep qiye /etc/hosts` 应看到 `172.24.0.4`）             |
| `Connection refused/timeout` 到中继 | `SMTP_RELAY_LISTEN_HOST` 是否等于 pg-egress 当前 IP                                              |
| 有 `EmailNotificationLog` 但不发    | 用 4.3 的查询看 `retry_count` 是否已达 3；看 worker 日志里的 `plane.exception`                   |
| 某些人收不到                        | 个人设置 Profile → Notifications 的开关；是否是项目 active 成员；是否是自己操作自己              |
| 邮件内容里的链接不可点              | 检查 `WEB_URL`（origin 过期后的回退值）                                                          |
