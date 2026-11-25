# 04 数据库设计

状态 **设计基线 v0.2** · PostgreSQL 17

![数据库 ER 图](diagrams/database-er.png)

图保留 v0.1 的核心关系。v0.2 新增审计、用量和预算，完整约束以本页为准。

## 约定

业务主键用 UUID，外部 JSON 使用 UUID 字符串。时间字段用 `timestamptz`，API 以 UTC/RFC 3339 表达。金额用 `numeric(18,8)` 并保存币种、价格版本和已知/未知状态。载荷用 JSONB，服务端用 Pydantic 校验；索引优先覆盖规范化关系字段。所有表默认非空，表中 `?` 代表允许 NULL。

## 迁移分批

- M1 创建 `datasets`、`dataset_versions`、`dataset_items`、`model_configs`、`audit_events`。M1 只开放 LLM 与 fake 配置，预留类型枚举供后续阶段使用。
- M2 增加评分配置、运行、执行项、尝试、候选工件、评分结果和发件箱。M3 添加用量记录及运行预算，M4 添加工具轨迹并写入完整 Agent 快照。M6 添加回归报告。

## 表定义

| 表 | 字段设计 | 约束与一致性 |
| --- | --- | --- |
| `datasets` | `id, name, kind, description, created_by, created_at, archived_at?` | `UNIQUE(name)`；kind 为 llm 或 agent；归档后历史版本可读，禁止新版本、导入、发布及新运行 |
| `dataset_versions` | `id, dataset_id, version_no, status, checksum?, item_count, created_at, published_at?` | `UNIQUE(dataset_id,version_no)`；draft/published；发布时锁版本行，固定校验和、顺序和数量 |
| `dataset_items` | `id, dataset_version_id, case_key, ordinal, input JSONB, expectation JSONB, tags text[], created_at` | `UNIQUE(dataset_version_id,case_key)`、`UNIQUE(dataset_version_id,ordinal)`；版本发布后禁止变更 |
| `model_configs` | `id, config_key, revision, kind, adapter_key, provider, model_name, provider_endpoint_ref, credential_ref?, parameters JSONB, created_at, disabled_at?` | `UNIQUE(config_key,revision)`；用于候选或裁判；revision 内执行配置不可编辑；禁用影响新运行 |
| `evaluation_profiles` | `id, profile_key, revision, kind, evaluator_config JSONB, judge_model_config_id?, created_at, disabled_at?` | `UNIQUE(profile_key,revision)`；固定规则、rubric、Judge prompt 和指标版本 |
| `runs` | `id, dataset_version_id, evaluation_profile_id, baseline_run_id?, created_by, idempotency_key, request_hash, status, repeat_count, total_items, token_budget?, cost_budget?, budget_currency?, max_calls?, created_at, started_at?, finished_at?, cancel_requested_at?` | `UNIQUE(created_by,idempotency_key)`；单份请求摘要；从有效明细推导进度 |
| `run_targets` | `id, run_id, model_config_id, ordinal, execution_snapshot JSONB` | `UNIQUE(run_id,model_config_id)`、`UNIQUE(run_id,ordinal)`；快照保存已解析的非秘密执行与评测版本 |
| `run_items` | `id, run_id, run_target_id, dataset_version_id, dataset_item_id, repeat_no, status, attempt_count, dispatch_seq, available_at, dispatched_at?, redispatch_count, lease_token?, lease_until?, updated_at` | `UNIQUE(run_target_id,dataset_item_id,repeat_no)`；组合外键强制目标、样本与运行使用对应版本 |
| `execution_attempts` | `id, run_item_id, attempt_no, stage, outcome?, error_code?, started_at, ended_at?` | `UNIQUE(run_item_id,attempt_no)`；结束失败、重试、过期或取消均有记录，未知用量可为空 |
| `candidate_artifacts` | `run_item_id PK/FK, source_attempt_id, output JSONB, finish_reason, latency_ms, created_at` | 每执行项一个已提交工件；和最终候选工具轨迹同事务持久化，评分重试可复用 |
| `tool_calls` | `id, attempt_id, ordinal, tool_name, argument_summary JSONB, output_summary JSONB?, status, latency_ms?, error_code?` | `UNIQUE(attempt_id,ordinal)`；有效轨迹由工件的 `source_attempt_id` 决定；失败/中断轨迹可按尝试排查 |
| `evaluation_results` | `run_item_id PK/FK, attempt_id?, outcome, final_score?, rule_score?, judge_score?, task_completed?, error_code?, evidence JSONB, created_at` | 每终态执行项一条，取消可无尝试；分数 `numeric(5,4)` 且 0–1。失败和取消的最终分为空 |
| `outbox_events` | `id, run_item_id, dispatch_seq, available_at, publish_token?, publish_lease_until?, sent_at?, exhausted_at?, publish_attempts, last_error?` | `UNIQUE(run_item_id,dispatch_seq)`；发布超时可重领，独立发布令牌保护回写，耗尽事件退出派发扫描 |
| `usage_records` | `id, run_item_id, attempt_id, stage, call_no, reserved_tokens, reserved_cost, input_tokens?, output_tokens?, total_tokens?, input_cost?, output_cost?, currency, pricing_version, cost_status, latency_ms?, error_code?, created_at, settled_at?` | stage 为 candidate/judge，`UNIQUE(attempt_id,stage,call_no)`，复合外键约束所属执行项 |
| `audit_events` | `id, actor_id, action, resource_type, resource_id, request_id, metadata JSONB, created_at` | 追加写入。按 `(resource_type,resource_id,created_at,id)` 索引，不记录敏感原文 |
| `regression_reports` | `id, run_id, target_id, baseline_run_id, baseline_target_id, policy_revision, status, report JSONB, created_at` | M6，两个目标分别约束到所属运行，按目标配对及策略版本唯一，status 为 passed/failed/insufficient_evidence |

## 约束和访问路径

- 为 `runs(id,dataset_version_id)`、`run_targets(id,run_id)`、`dataset_items(id,dataset_version_id)` 建组合唯一约束，供 `run_items` 的组合外键使用。结果和候选工件的尝试 ID 以 `(attempt_id,run_item_id)` 约束同一执行项。
- 外键默认 `RESTRICT`。计数、顺序、耗时和 token 非负，`repeat_no` 从 1 开始。用 CHECK 校验状态值、终态时间和租约字段配对。状态转换由事务条件更新校验。
- 数据集发布和导入共用版本行锁。发布时按 ordinal 升序构造对象数组，每项包含 `case_key, ordinal, input, expectation, tags`。从数据库读回规范值，按键排序、无多余空白、保留数组顺序，用无 BOM 的 UTF-8 JSON 字节计算 SHA-256 十六进制 checksum。发布时间和 UUID 不参与计算。禁止更新已发布样本，测试覆盖仓储路径及并发导入。
- 列表索引 `runs(created_at DESC,id DESC)`、`run_items(run_id,status,id)`、`run_items(run_target_id,id)`。到期项索引对 queued/retry_wait 的 `(available_at,id)` 和 running 的 `(lease_until,id)` 分别建立部分索引。
- 发件箱使用 `(available_at,id) WHERE sent_at IS NULL AND exhausted_at IS NULL` 的部分索引。消息发布频率受冷却限制，清理已发送事件须确保对应执行项已终止并满足审计窗口。

## 数据库时间与锁顺序

- 领取、回收、写回和派发共用 PostgreSQL 时间。锁行后在同一事务取一次 `clock_timestamp()` 判断，避免以事务开始时间或应用主机时间误判。已过期定义为 `lease_until <= db_now`。旧 Worker 写回要求 status 为 running、令牌相符且 `lease_until > db_now`，否则不写工件、结果、尝试或用量。 [PostgreSQL 17 时间函数](https://www.postgresql.org/docs/17/functions-datetime.html#FUNCTIONS-DATETIME-CURRENT)
- 涉及运行的写事务依次锁 `runs → run_items → execution_attempts → usage_records/outbox_events`。批量项按 ID 顺序锁，锁后重新验证。发布者仅在短事务锁 outbox 行，归还时同样遵守顺序，不持 outbox 锁再取上游锁。数据集写入按 `datasets → dataset_versions` 锁定。
- `run_items.available_at` 表示执行项何时可被领取，负责业务退避。 `outbox_events.available_at` 表示某条消息何时可被派发，负责 broker 发布退避。事件可晚于执行项生效，消息到达后仍需核验执行项时间和序号。

## 用量与预算

- 每次外部调用在发送前插入一条预留，使用 attempt 内从 1 开始递增的 `call_no` 区分调用，Agent 的多步模型请求均为 candidate。价格版本、币种及最大 Token/费用在调用前冻结，金额预留向上取到 8 位小数。预算由锁住的 `runs` 行串行化，基线可在该事务汇总用量记录，避免多 Worker 同时超额预留。
- `cost_status` 枚举为 reserved、calculated、unknown。calculated 的输入和输出费用由版本化单价乘 Token，缺失任一组成部分时总费用未知，未知分量保留 NULL。Token 与费用分别按已覆盖数和预留数汇总。reserved 和 unknown 按保守预留上限占额度，已知维度按结算值占额度。调用次数按已创建的预留行计算。
- 实际已提交用量优先统计，`attempt_no > 1` 为重试切片，可另按阶段分组；同一 `call_no` 只结算一次。租约过期时 Dispatcher 将未结算记录置为 unknown，保留上限占用。Fake 不发外部请求，M2 单靠尝试次数限制运行，不生成真实用量账单。

## 版本快照与审计

`run_targets.execution_snapshot` 保存数据 checksum、候选/评分配置 revision、检测规则、Judge 配置和版本化价格引用。M4 的 Agent 额外固定 `agent_graph_version`、`tool_registry_version`、`prompt_version`、`git_commit_sha` 以及 `runtime_dependency_versions`，至少涵盖 Python、LangGraph、模型适配器依赖及锁文件摘要。预发从运行详情核对快照与运行工件，缺必需快照字段时拒绝新运行。

审计动作包括 `model_config.created`、`model_config.disabled`、`evaluation_profile.created`、`evaluation_profile.disabled`、`dataset_version.published`、`run.cancel_requested`。动作首次生效与审计插入共用事务；重复发布、禁用和取消按原语义返回，幂等重放不重复写同动作。审计仅保存资源标识、前后状态摘要及 Request ID，通过受控只读通道访问。

## 事务、迁移与保留

创建运行整批提交。最终完成时在单事务写结果并推进状态。归档数据集不删除历史运行；证据过期按审批执行内容脱敏、清理或整组删除，备份遵循同等留存策略。

Alembic 管理版本，发布前用空库和上一版脱敏快照验证升级。增量迁移按扩展、回填、收缩推进。大表索引构建和回填需在预发演练锁影响。生产在线数据、评分证据和备份的保留期限见 [待确认决策](09-development-progress.md#待确认决策)。
