# AGENTS.md

## 项目与阶段

本项目为内部 LLM 与 Agent 回归评测平台。当前设计基线 v0.2，M1 仅搭建 FastAPI、PostgreSQL 与 Fake/规则链路。M2 才启用 Celery/Redis 和正式运行 API。M3 接 Judge、真实供应商和用量，M4 接 LangGraph，M5 交付 Vue 3，M6 增加 P1 回归报告。开始前读 [任务清单](docs/10-implementation-tasks.md)、[阶段边界](docs/03-architecture.md) 和所改接口的契约。

技术栈使用 Python 3.12、uv、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 17、Celery、Redis、LangGraph、Vue 3、TypeScript、Docker Compose、pytest、Ruff、GitHub Actions。依赖经 uv 锁定。

## 目录和职责

| 目录 | 职责 |
| --- | --- |
| `backend/app/api/` | HTTP 路由、认证授权、协议校验和错误映射，调用领域服务 |
| `backend/app/schemas/` | 请求、响应和边界的数据模型 |
| `backend/app/domain/` | 业务流程、状态、事务边界 |
| `backend/app/db/` 与 `alembic/` | ORM、仓储、Session 和迁移 |
| `backend/app/adapters/` 与 `backend/app/evaluation/` | 模型 I/O 适配与纯评分模块 |
| `tests/`、`scripts/`、`frontend/` | 测试、开发/运维入口和 M5 管理台 |

## 编码和数据规则

- 模块、函数、变量使用 snake_case，类使用 PascalCase，常量使用 UPPER_SNAKE_CASE。公开函数标注类型，保持函数短小和职责单一，提交前运行 Ruff。
- Pydantic 校验全部外部输入和输出边界。请求、响应、ORM 分开建模，枚举和默认值与 API 一致，外部对象拒绝额外字段。禁止绕开校验写库。
- Router 不承载业务和长时间模型 I/O。M1 在领域服务、测试或脚本验证 Fake。禁止用 FastAPI BackgroundTasks 冒充生产任务系统。
- 使用请求或工作单元级 Session，禁止全局共享 Session。事务在应用服务或工作单元收口，模型 I/O 在事务外。运行与数据集锁序、租约时间遵循数据库文档。
- 数据结构变动必须由 Alembic 管理。提交迁移前检查约束、索引及 upgrade 路径，降级或恢复只在隔离环境验证。禁止通过启动时 create_all 修改生产 schema。

## API、错误、日志与安全

- 业务路由固定 `/api/v1`，沿用契约中的状态码、分页和幂等规则。Pydantic 错误转为统一错误体及 Request ID，含可处理的业务错误码。
- 捕获有明确处理策略的异常。区分低分、业务错误、可重试外部错误和基础设施失效。响应不包含栈追踪、内部路径、凭据或未脱敏载荷。
- 结构化日志使用允许列表，并串联 Request ID、Run ID、Item ID 和 attempt_no。禁止打印 API Key、Authorization、连接串、完整敏感请求与模型输出。
- 密钥从服务端配置注入。禁止接受任意 Endpoint 或从前端传密钥；真实调用遵循白名单、出站校验、超时和预算。权限必须在服务端验证。

## 测试、Git 和 AI 工作流

1. 修改前检查 Git 状态、现有代码和相关文档，确认当前阶段、前置任务及文件范围。已有用户改动须保留。
2. 每个会话只做一个小任务，按 Prompt 分步实现。每项功能都有对应测试，按需使用真实 PostgreSQL、Fake Provider 或故障夹具。
3. 运行改动相关的 Ruff、pytest、迁移或构建检查，报告命令及实际通过、失败和未执行项。未测试的功能不得标为完成。
4. 每个提交对应一个有验收的任务，消息描述行为，先审查 diff 和敏感文件。按用户授权提交或推送，禁止提交未经检查的改动。
5. 变更既定存储或队列、执行语义、API 契约、状态机、锁顺序、评分和预算口径、外部数据边界、里程碑范围前，先提出依据并获得范围确认。禁止批量生成无当前用途的模块。
