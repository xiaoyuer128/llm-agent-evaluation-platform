# 10 M1 实施任务

状态 **设计基线 v0.2**。各任务均待开发。

M1 的验收链为数据集 → 已发布样本 → Fake Model Provider → 确定性评分。正式运行 API 从 M2 开放。下列命令为任务交付后的验收入口，依赖 Docker 的命令使用隔离开发或测试数据库。集成用例在缺少测试库时应显式失败或由门禁报告未执行。

## 执行顺序

```text
M1-01 → M1-02 → M1-03 ┐
            └─→ M1-04 ┴→ M1-05 → M1-06 ┐
M1-01 ─────────→ M1-07 ────────────────┴→ M1-08 → M1-09
```

M1-03 与 M1-04 可并行开展 API 和数据库工作，按 M1-03 先合入、M1-04 再接 readiness 的顺序收敛。M1-05 与 M1-06 共享 Router 注册和审计契约，顺序合入。M1-07 只维护纯评分器，可与基础设施和管理 API 并行。下游任务在前置任务验收后启动。对应提示见 [Vibe Coding Prompts](11-vibe-coding-prompts.md)。

## M1-01 工程初始化

| 项 | 说明 |
| --- | --- |
| 任务目标 | 形成可锁定、可执行的 Python 3.12 项目骨架。 |
| 前置依赖 | 无。 |
| 文件范围 | `.python-version`、`pyproject.toml`、`uv.lock`、`backend/__init__.py`、`backend/app/__init__.py`、`tests/unit/test_environment.py`、`.gitignore` |
| 验收条件 | 在 Python 3.12 上依赖可从锁文件安装，Ruff 可执行，pytest 能收集且通过环境测试。 |
| 测试用例 | 环境版本为 3.12；应用包可导入；pytest 标记已注册。 |
| 完成标准 | 提交 pyproject.toml 和 uv.lock，记录版本、命令和实际输出。 |
| 常见风险 | 使用全局 Python 或手写锁文件会造成机器差异；预先创建空业务模块会模糊范围。 |

1. 固定 Python 3.12 项目范围，配置 uv、Ruff、pytest 路径与 integration/live 标记。
2. 锁定本阶段需要的 FastAPI、Pydantic 2、Pydantic Settings、Uvicorn、SQLAlchemy、psycopg、Alembic、pytest 和 HTTPX 测试依赖。
3. 只创建实际承担导入或测试职责的骨架，写可检查 Python 版本和包导入的测试。

```text
uv sync --locked
uv run --locked python --version
uv run --locked ruff check .
uv run --locked pytest tests/unit/test_environment.py
```

本任务不写 Router、数据库、业务服务或 CI。

## M1-02 配置与日志

| 项 | 说明 |
| --- | --- |
| 任务目标 | 建立环境配置、结构化日志、Request ID 和脱敏边界。 |
| 前置依赖 | M1-01。 |
| 文件范围 | `.env.example`、`backend/app/core/settings.py`、`backend/app/core/logging.py`、`backend/app/core/request_context.py`、`tests/unit/test_settings.py`、`tests/unit/test_logging.py` |
| 验收条件 | 缺关键配置时错误可定位且不泄密；Request ID 同上下文一致并在请求结束后清除；生产环境拒绝开发身份。 |
| 测试用例 | 环境优先级、无效值、本地身份隔离、日志脱敏和上下文隔离；为 T19/T28 提供底层覆盖。 |
| 完成标准 | 配置模板仅含占位，测试使用临时配置，不依赖开发者机器。 |
| 常见风险 | ContextVar 未 reset 会串请求；repr 与异常文本可能间接泄露 DSN 或凭据。 |

1. 以 Pydantic Settings 解析环境、DB DSN、日志级别、游标签名密钥和本地开发身份配置。
2. 采用白名单日志字段、JSON 输出与 ContextVar 传播 Request ID，校验外部 ID 格式和长度，非法时生成新 ID。
3. 使用 SecretStr 或等效保护，并用测试验证设置和异常日志无秘密，不写原始 body。

```text
uv run --locked ruff check .
uv run --locked pytest tests/unit/test_settings.py tests/unit/test_logging.py
```

不引入远程日志 SDK，不将真实凭据写入样例。

## M1-03 FastAPI 基础工程

| 项 | 说明 |
| --- | --- |
| 任务目标 | 提供可测试的应用工厂、路由、认证边界、健康探针和稳定错误结构。 |
| 前置依赖 | M1-02。 |
| 文件范围 | `backend/app/main.py`、`backend/app/api/router.py`、`backend/app/api/health.py`、`backend/app/api/errors.py`、`backend/app/api/security.py`、`backend/app/api/middleware.py`、`tests/api/test_health.py`、`tests/api/test_error_contract.py` |
| 验收条件 | 探针、Swagger、角色依赖与错误结构符合 API 规范，校验错误响应不回显非法原值。 |
| 测试用例 | T28 的 live、ready 成功/失败依赖、Request ID、错误结构和 OpenAPI。 |
| 完成标准 | 接口契约测试通过，已标明 readiness 真实数据库接线依赖 M1-04。 |
| 常见风险 | 处理器吞异常导致返回 200；把开发 bearer 误开到生产。 |

1. 建立 app 工厂和 /api/v1 路由入口，启用 Swagger，注册结构化错误映射和 Request ID 中间件。
2. 按 viewer/operator/admin 定义依赖接口，本地仅接受受控开发身份，测试使用依赖注入。
3. 实现 live 和可注入的 ready 检查，真实 DB 检查在 M1-04 接线，未接线或检查失败返回 503。

```text
uv run --locked ruff check .
uv run --locked pytest tests/api/test_health.py tests/api/test_error_contract.py
```

不注册 /api/v1/runs，不做模型请求，不使用 BackgroundTasks 执行评测。

## M1-04 PostgreSQL 与迁移

| 项 | 说明 |
| --- | --- |
| 任务目标 | 交付真实 PostgreSQL 17、每工作单元独立 Session 及首批可回放迁移。 |
| 前置依赖 | M1-02。若 M1-03 未合入，先完成数据库部分，接 readiness 后再验收。 |
| 文件范围 | `docker-compose.yml`、`alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/0001_m1_core.py`、`backend/app/db/base.py`、`backend/app/db/session.py`、`backend/app/db/models.py`、`backend/app/main.py（仅 readiness 接线）`、`tests/integration/conftest.py`、`tests/integration/test_migrations.py` |
| 验收条件 | 空库迁移可重复验证，期望约束生效，探针能识别 DB 断连和版本落后。 |
| 测试用例 | T24；源及目标 schema、重复升级、已知约束违例和 readiness。 |
| 完成标准 | 保存真实 PostgreSQL 迁移和测试证据；降级仅在隔离测试库运行。 |
| 常见风险 | SQLite 掩盖 PostgreSQL 锁和 JSONB 行为；导入模型时偷偷 create_all 会绕开迁移。 |

1. Compose 启动 PostgreSQL 17，开发仅绑定 loopback，检查数据库就绪。
2. 定义 Engine、Session 工厂及 Base，迁移创建 M1 五张表并检查唯一键、外键和索引。
3. 以 PostgreSQL 查询和当前 Alembic 版本验证 readiness，在临时数据库演练 upgrade/downgrade/upgrade。

```text
docker compose up -d --wait postgres
uv run --locked alembic upgrade head
uv run --locked pytest -m integration tests/integration/test_migrations.py
```

不添加 Redis、运行表或队列，不共享全局 SQLAlchemy Session。

## M1-05 数据集和 JSONL

| 项 | 说明 |
| --- | --- |
| 任务目标 | 交付数据集、版本、JSONL 预览/导入、发布和归档。 |
| 前置依赖 | M1-03、M1-04。 |
| 文件范围 | `backend/app/api/router.py`、`backend/app/api/datasets.py`、`backend/app/schemas/datasets.py`、`backend/app/domain/datasets.py`、`backend/app/db/datasets.py`、`backend/app/db/audit.py`、`tests/api/test_datasets.py`、`tests/integration/test_dataset_versions.py` |
| 验收条件 | 合法预览零写入，错误行返回 422，失败批次零残留，发布与并发导入一致，归档历史可查询。 |
| 测试用例 | T02/T03，含 query/body 混淆、大小、并发、子资源归属、重复发布和不可变性。 |
| 完成标准 | 核对 OpenAPI 与 JSONL 示例，保存 PostgreSQL 并发测试结果。 |
| 常见风险 | 仅靠应用初次读校验会出现发布竞态；逐行提交会留下半批数据。 |

1. 实现数据集和版本请求响应、排序及角色验证。
2. 流式读取大小受限的 JSONL 并用 Pydantic 严格验证；追加前再次在锁内校验草稿状态及 case_key，事务整体失败时回滚。
3. 发布按样本序列生成 checksum，原子冻结版本及写审计；重复发布返回原资源。归档停止后续写入与新运行。

```text
uv run --locked ruff check .
uv run --locked pytest tests/api/test_datasets.py
uv run --locked pytest -m integration tests/integration/test_dataset_versions.py
```

不使用临时接口，不创建可编辑已发布样本的路径。

## M1-06 模型配置与 Fake Provider

| 项 | 说明 |
| --- | --- |
| 任务目标 | 提供管理配置、模型适配器协议与可复现的 Fake 提供方。 |
| 前置依赖 | M1-05，复用路由注册与审计写入。 |
| 文件范围 | `backend/app/api/router.py`、`backend/app/api/model_configs.py`、`backend/app/schemas/model_configs.py`、`backend/app/domain/model_configs.py`、`backend/app/db/model_configs.py`、`backend/app/adapters/base.py`、`backend/app/adapters/fake.py`、`tests/unit/test_fake_provider.py`、`tests/api/test_model_configs.py` |
| 验收条件 | 配置被正确授权，秘密字段拒绝且不回显，禁用保留旧 revision，Fake 对相同输入稳定。 |
| 测试用例 | T01/T19，覆盖危险参数、无权限、审计、明确超时与缺使用量。 |
| 完成标准 | 适配器契约和日志输出检查通过，记录执行命令与结果。 |
| 常见风险 | 测试把缺失 Token 当零；把 API 请求中的 URL 或秘密透传给适配器。 |

1. 实现本地仅允许 kind=llm、注册的 fake 配置，按 config_key 创建 revision，首次禁用和生效审计同事务。
2. 定义 ModelRequest/ModelResponse 与适配器错误类型，响应含 output、latency_ms、usage 或 null、结束原因。
3. 根据夹具键和输入输出确定性结果，可注入超时和错误，避免外部请求和不稳定 sleep 测试。

```text
uv run --locked ruff check .
uv run --locked pytest tests/unit/test_fake_provider.py tests/api/test_model_configs.py
```

不接真实模型，不允许前端传秘密或任意 URL。

## M1-07 确定性评分器

| 项 | 说明 |
| --- | --- |
| 任务目标 | 实现可独立调用的 Exact Match、Numeric Comparison、JSON Comparison 和 ScoreResult。 |
| 前置依赖 | M1-01。可与 M1-02–M1-06 并行实施。 |
| 文件范围 | `backend/app/evaluation/rules.py`、`backend/app/evaluation/types.py`、`tests/unit/test_scoring.py` |
| 验收条件 | 三类评分与规则权重可预测，异常、零分及 null 可区分，无外部 I/O。 |
| 测试用例 | T09，覆盖 Unicode、单位、边界、NaN/Infinity、bool 与数字、列表顺序和额外字段。 |
| 完成标准 | 纯 Python 测试通过，资料中的规则示例可复算。 |
| 常见风险 | Python 将 bool 视为 int，浮点计算造成边界误判；误将坏期望计入低分。 |

1. 按配置执行 NFKC、strip/casefold、Decimal 容差和严格 JSON 类型比较。
2. 实现权重与硬门禁和证据，明确候选错误格式的有效零分与坏期望导致的技术失败。
3. 使用独立期望值测边界并验证 4 位小数舍入。

```text
uv run --locked ruff check .
uv run --locked pytest tests/unit/test_scoring.py
```

不依赖 Router、数据库、Judge 或 LangGraph。

## M1-08 基础集成验证

| 项 | 说明 |
| --- | --- |
| 任务目标 | 以真实 PostgreSQL 和本地 Fake 完成数据集到评分的可复现冒烟。 |
| 前置依赖 | M1-05、M1-06、M1-07。 |
| 文件范围 | `backend/app/domain/evaluate_sample.py`、`scripts/smoke_m1.py`、`Dockerfile`、`docker-compose.yml`、`tests/integration/test_m1_smoke.py` |
| 验收条件 | Compose 能启动，脚本能读已发布样本、返回预期评测结果；注入失败时返回非零。 |
| 测试用例 | T29 和 T24 冒烟复核；校验固定分值及错误出口。 |
| 完成标准 | 报告命令、容器状态和脚本结果，保留测试证据。 |
| 常见风险 | 将 M1 冒烟变成临时运行 HTTP 接口；测试写入真实业务库。 |

1. 把已发布样本读取与 Fake/规则评分通过同步领域服务组合，配置/样本可追溯，评分输出为 JSON。
2. 补 Compose 的 migrate 与 api，用一次性迁移和非 root 应用镜像；脚本以唯一名称使用真实 PostgreSQL 夹具。
3. 接通数据库与 API 管理流程，执行领域冒烟并处理非零退出。

```text
docker compose config --quiet
docker compose up -d --build
uv run --locked pytest -m integration tests/integration/test_m1_smoke.py
uv run --locked python -m scripts.smoke_m1
```

不加 Celery、Vue、Judge、LangGraph、生产配置和运行 API。

## M1-09 CI 基础设施

| 项 | 说明 |
| --- | --- |
| 任务目标 | 让 GitHub Actions 可重复执行 M1 静态、契约、真实 DB 和构建检查。 |
| 前置依赖 | M1-08。 |
| 文件范围 | `.github/workflows/ci.yml`、`README.md（仅追加已验证的 CI 说明）` |
| 验收条件 | CI 能区分静态、测试、迁移及构建错误，所有 M1 门禁均有可复验记录。 |
| 测试用例 | 跑 M1 用例 T01/T02/T03/T09/T19/T24/T28/T29；基线构建可重复。 |
| 完成标准 | 记录提交 SHA、检查结果和工作流链接。CI 未运行或失败时保持该项待验收。 |
| 常见风险 | pytest 跳过 DB 集成导致假通过；CIDB 凭据被当成生产配置；用过宽权限发布构建。 |

1. 配置 Linux CI 的 Python 3.12、uv 锁定安装和 PostgreSQL 17 测试实例。
2. 执行 Ruff、pytest unit/API、PostgreSQL integration 与应用镜像构建，保留失败日志或 JUnit。
3. 使用最小权限，不注入付费密钥；先检查工作流和本地等价命令，再报告真实 Actions 状态。

```text
uv sync --locked
uv run --locked ruff check .
uv run --locked pytest -m "not integration and not live"
uv run --locked pytest -m integration
docker compose config --quiet
docker compose build api
```

不扩大权限，不虚构远端 CI 结果，不引入发布或生产部署步骤。
