# 08 部署与运维

状态 **设计基线 v0.1**。以下服务名、配置和命令是 M1 起的交付契约，当前尚无运行环境。

## 环境布局

| 环境 | 服务 | 数据与访问 |
| --- | --- | --- |
| 本地与 CI | M1 启动 api、migrate、postgres、redis，M2 加入 worker 和 dispatcher | M2 默认确定性 fake 模型，API 可绑定 127.0.0.1，数据库和 Redis 仅容器网 |
| 预发 | 与生产同镜像，将数据库和 Redis 置于独立网络 | 经审批的测试密钥、配额和脱敏集，验证迁移、积压和备份 |
| 生产 | TLS 网关、API 实例、Worker 池、调度实例、独立 PostgreSQL/Redis | 部署平台待确认；网关验证身份，应用校验授权；供应商和数据库走受控网络 |

API、Worker、dispatcher 与 migrate 复用应用镜像，使用非 root 用户。Compose 通过 `service_healthy` 等待基础设施，通过 `service_completed_successfully` 等待迁移。生产以单独的迁移作业控制 schema 版本和升级顺序，不由 API 副本并行迁移。 [Docker Compose 启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)

## 配置契约

| 配置 | 说明 |
| --- | --- |
| APP_ENV、LOG_LEVEL | 环境标识和结构化日志级别 |
| DATABASE_URL、CELERY_BROKER_URL | 由环境或密钥服务注入，生产开启加密和最小权限 |
| AUTH_ISSUER、AUTH_AUDIENCE | 企业身份源接入后启用。开发测试通过隔离身份配置 |
| MODEL_PROVIDER_MODE、MODEL_CONFIG_FILE | 本地默认 fake；真实模式读取受控服务端配置与秘密引用 |
| MAX_RUN_ITEMS、WORKER_CONCURRENCY | 首轮单次运行上限 1,000；Worker 并发按队列压测确定 |
| CANDIDATE_TIMEOUT_S、JUDGE_TIMEOUT_S、ITEM_LEASE_S | 初始值 25、25、150；与任务硬超时和 Redis 可见性配置一起验证 |
| DISPATCH_INTERVAL_S、REDIS_VISIBILITY_TIMEOUT_S | 调度扫描间隔与 300 秒可见性初始值 |
| OTEL_EXPORTER_OTLP_ENDPOINT | 可选，配置日志和链路采集目标 |

Redis 使用独立实例或专用部署，启用持久化与 noeviction 策略。Redis 发生数据丢失时由 PostgreSQL 恢复派发。真实端点只从受控配置读取；镜像、日志和公开仓库不保存环境密钥或生产样本。

## M1 本地和 CI 启动顺序

```powershell
Copy-Item .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose ps -a
Invoke-RestMethod http://localhost:8000/health/ready
docker compose logs --tail=100 api migrate
```

迁移作业应成功退出，其余进程应运行且必要依赖就绪。M1 冒烟验证探针和数据集导入发布。M2 加入 fake 模型端到端脚本，验证提交运行、按 ID 查询和结果对账。查看失败时先定位 `run_id`，再按执行项和尝试号读脱敏日志。正常停机使用 `docker compose down` 保留数据卷。

## 发布和回滚

1. CI 运行 lint、契约、基础设施与迁移检查，构建按摘要标识的镜像，记录提交、锁文件、镜像与迁移版本。
2. 预发恢复脱敏数据，验证迁移、从 Redis 中断恢复、Worker 优雅退出及 fake 端到端。
3. 生产发布前确认备份点及数据库升级路径。先执行向后兼容扩展，再滚动发布 API、调度和 Worker，必要时暂停提交并排空在途任务。待旧版全退后收缩 schema。
4. API 探针、积压和错误率恶化时停止扩大发布。兼容 schema 下恢复上一版镜像；破坏性变化按数据恢复预案执行，先保留事故证据与恢复点。

## 监控和故障处置

| 信号 | 处置 |
| --- | --- |
| API P95 > 500 ms 或 5xx ≥ 1% 持续 10 分钟（试点阈值） | 核对 DB 连接池、锁等待、慢查询与新版本，必要时回滚 API |
| 最老待派发项 > 120 秒、队列持续增长（试点阈值） | 检查 Redis、调度租约、Worker 并发和供应商配额，恢复后核对执行项和结果 |
| 429 或供应商错误突增 | 分候选/裁判确认请求预算，降低并发、暂停该配置，避免手动全量重投 |
| 租约回收、重试耗尽、裁判解析失败突增 | 检查硬超时、供应商 schema 与最近发布，按错误码隔离配置 |
| 数据库不可用 | API 返回 503；恢复连接后确认迁移版本和活跃租约，再恢复派发 |
| 日志疑似泄露 | 关闭相关字段输出、轮换凭据、按组织流程审计访问和影响 |

所有告警由生产值班人或团队接收，标签使用有限枚举。准备度检查数据库和 schema，Redis 与供应商健康通过独立监测体现。

## 备份和恢复

生产候选方案为日常自动备份及可用时点恢复的 PostgreSQL 备份，定期恢复到隔离环境。记录最近可恢复时间、实际恢复时长和校验和。恢复前暂停写入和派发，避免旧消息重复执行；恢复后使用干净/独立的 broker 命名空间，从数据库状态扫描恢复非终态执行项，按样本、运行和结果对账后逐步放量。备份加密、保留期和恢复指标由正式环境决策确认。
