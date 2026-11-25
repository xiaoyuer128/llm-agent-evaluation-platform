# 02 技术选型

状态 **设计基线 v0.1**。生产供应商、身份源和资源额度见 [待确认决策](09-development-progress.md#待确认决策)。

| 层 | 建议基线 | 决策依据 |
| --- | --- | --- |
| Python | Python 3.12、uv、pyproject.toml 与 uv.lock | API、调度、驱动、评分与测试使用同一语言。构建与 CI 执行 `uv sync --locked`，版本升级需重跑契约及迁移测试 |
| HTTP | FastAPI、Pydantic 2、Uvicorn | 请求校验与 OpenAPI 契约共用模型；首期同步路由处理限长导入和数据库事务，Web 层不执行模型请求 |
| 数据 | PostgreSQL 17、SQLAlchemy 2.0、psycopg 3、Alembic | 关系约束保护版本与结果完整性，JSONB 承载业务输入与评分扩展；使用显式事务、迁移与 PostgreSQL 集成测试 |
| 任务 | Celery 5.x、Redis 7.x、Linux Worker prefork | 任务按候选类型路由，与 HTTP 扩容解耦；应用靠 PostgreSQL 的任务状态、事务发件箱和租约恢复 |
| 模型接入 | HTTPX、OpenAI 兼容适配器（拟定）、确定性 fake 适配器 | 统一请求、超时、用量和错误；兼容性以模型契约测试为准。模型端点从受控配置解析，客户端仅提交配置 ID |
| Agent | LangGraph Python、注册式工具 | 图节点和工具调用边界清晰；每次候选执行启动独立图实例状态，首期由评测任务重试和工件恢复处理失败 |
| 评测 | 自有确定性评分器、结构化 LLM 裁判、pytest 校准脚本 | 指标口径与失败类型由平台管理；裁判输入、规则与版本可审计 |
| 质量 | pytest、HTTPX TestClient、Ruff、GitHub Actions | 单元/契约、真实基础设施集成、故障注入分开执行；默认 CI 不使用付费密钥 |
| 运维 | Docker Compose、结构化日志、Prometheus 指标 | 单机开发/预发可复现；生产存储、网关和告警接入见部署设计 |

## 决策记录

- **ADR-01 同步数据库访问。** HTTP 请求和 Worker 并发由 Web/Worker 进程与线程边界管理。每请求或每进程工作单元使用独立 Session，模型调用前后分开事务，防止 DB 连接被远程 I/O 长时间占用。评估异步化时以压测证据为依据。 [SQLAlchemy Session 文档](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- **ADR-02 PostgreSQL 保存业务结果。** Redis 承担 Celery 消息传递及限速辅助，Celery 禁用业务结果后端。消息允许重投，应用对逻辑执行去重，并由恢复扫描处理过期租约或丢失唤醒。迁移到 RabbitMQ 需用实际积压和运维要求评估。 [Celery Redis 文档](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- **ADR-03 LangGraph 用于 Agent 流程。** 状态图连接模型节点、工具节点和终止节点，平台任务表负责批量运行状态。首期使用有界的只读模拟工具。需要跨进程续跑真实副作用时，再引入 checkpoint 与工具幂等协议。 [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- **ADR-04 Compose 作为开发和预发入口。** 研发先以同一镜像派生 API、Worker、调度和迁移进程。生产拓扑取决于故障域、持久化和发布频率。 [Docker Compose 启停顺序](https://docs.docker.com/compose/how-tos/startup-order/)

所列 Python 依赖是基线系列。第一次应用构建时锁定包版本和容器 digest，核验 Linux 运行、数据库迁移及供应商契约，再记录在发布清单。 [PostgreSQL 版本支持](https://www.postgresql.org/support/versioning/)
