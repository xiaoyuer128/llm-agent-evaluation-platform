# 03 系统架构设计

状态 **设计基线 v0.2**

![系统架构图](diagrams/system-architecture.png)

上图为 v0.1 的目标拓扑。M1 的开发者通过 Swagger 和开发脚本操作，M5 的研发与 QA 通过 Vue 3 管理台访问同一 API。

## 阶段边界

| 阶段 | 实现与验收 |
| --- | --- |
| M1 基础实现 | FastAPI、PostgreSQL、SQLAlchemy、Alembic、数据集和配置基础 API。Fake Model Provider 和确定性评分器由同步领域服务组合，使用测试或 `scripts/smoke_m1.py` 验证真实 PostgreSQL 中的已发布样本。M1 不注册运行提交或查询路由 |
| M2 异步可靠性 | 使用 M1 的提供方与评分器，接入 Celery、Redis、Transactional Outbox、Worker Lease、Dispatcher、幂等、重试、取消与故障恢复。首次开放正式 `/api/v1/runs` 契约 |
| M3–M6 扩展 | M3 接入真实候选、Judge 和用量预算，M4 增加 LangGraph，M5 增加 Vue 3 和上线演练，M6 增加 P1 回归报告 |

API Router 只负责协议、授权和调用应用服务。M1 的进程内执行入口位于领域服务，后续也由 Worker 调用。长任务交给 M2 的队列系统，禁止用 FastAPI `BackgroundTasks` 承载评测。



## 组件边界

| 组件 | 职责 | 运行入口规划 |
| --- | --- | --- |
| Vue 3 管理台 | 数据集导入和发布、提交与取消运行、查询候选和样本证据 | `frontend/` |
| API | 鉴权、校验、版本发布、创建运行、读取进度和证据 | `backend.app.main:app` |
| 领域服务 | 数据集、运行状态、执行租约、评分协议、取消规则 | `backend/app/domain/` |
| 调度进程 | 扫描发件箱、派发和回收执行项、推进已取消项 | `python -m backend.app.dispatcher` |
| Celery Worker | 按执行项请求候选、记录工件、评分、回写结果 | `backend.app.worker:celery_app` |
| 适配与评分 | 统一模型、LangGraph 工具、规则和裁判适配器 | `backend/app/adapters/`、`backend/app/evaluation/` |
| PostgreSQL / Redis | PostgreSQL 持久化所有业务状态。Redis 传递唤醒消息和辅助限速 | 独立服务，使用私网 |

候选与裁判走独立并发和流量预算。执行队列区分 `eval_llm` 与 `eval_agent`。外部 I/O 发生在数据库事务之外。消息仅含执行项 ID 和发件箱事件 ID。M2 启用 `eval_llm`，M4 按需启用 `eval_agent`。

## 样本并行与进程边界

一个执行项对应「一个候选配置 × 一个数据集样本 × 一次重复」。M2 起每个 Celery prefork 子进程每次处理一个执行项。并行度由队列对应的 Worker 数量和进程并发配置提供。M3 的同一个执行项任务按候选调用 → 持久化工件 → Judge 评分执行，候选与 Judge 不拆队列。跨进程调用额度以阶段和供应商配置的 Redis 原子限额控制，有期限的许可在 finally 释放，过期回收。许可有效期覆盖调用期限，Redis 不可用时真实调用失败关闭。容量未获得时按有界退避回到数据库重试路径，不发出外部请求。供应商共用账号时分配阶段额度并校验总和。阶段分别统计 Token、费用、延迟和错误。基于实际瓶颈再考虑 Candidate Worker 与 Judge Worker 独立扩容。

压测确认主要瓶颈为模型 I/O 且供应商有剩余额度后，先试验单进程内有界并发处理彼此独立的请求。若单个执行项没有可并行的调用，提高每进程执行项数需要专门验证 Worker 执行方式，普通 prefork 子进程仍是一时一任务。试验必须保留逐项领取与写回的语义，并验证取消、超时、租约回收、重复投递和跨进程限速。具体压测见 [Worker 并发压测](06-test-plan.md#worker-并发压测)。

## M2 从提交到结果

1. API 按已发布版本枚举样本，校验候选类型和评分配置。在同一事务写入 `runs`、`run_targets`、`run_items`、初始 `outbox_events`，提交后响应 202。
2. 调度器依 `available_at` 领取待发送事件，使用 `FOR UPDATE SKIP LOCKED` 和发布租约。提交领取事务后向 Redis 发布 Celery 消息，再用发行令牌回写 `sent_at`。不确定是否已发布时允许重复发布。收到消息时，在数据库核对事件对应的执行项与当前 dispatch_seq。
3. Worker 在短事务领取到期执行项，增加尝试次数、分配随机租约令牌，写尝试记录。未到期、已持有有效租约或已终止的项直接退出。需要换阶段时先提交候选工件、轨迹和阶段状态，再执行评分。
4. Worker 完成后校验令牌和租约期限，在一个事务写结果、结束尝试并推进项及运行状态。每次完成、失败、取消或重试按固定顺序先锁运行行再锁执行项，汇总以执行项状态为准。终态结果唯一，读取时可从明细重建摘要。
5. 调度器周期性扫描发送超时事件、可能丢失唤醒的到期项和过期执行租约。生成带递增 `dispatch_seq` 的新事件。过期尝试按预算重新唤醒或落失败终态。旧 Worker 的写入需通过租约校验。

## 消息确认、期限与重试

M2 基线采用 `task_acks_late=True`、`task_reject_on_worker_lost=False`、`worker_prefetch_multiplier=1`、`task_soft_time_limit=100`、`task_time_limit=120`（秒）。执行项固定租约建议 150 秒，Redis `visibility_timeout` 建议 300 秒，覆盖正常预取等待与执行上限，Broker 配置见 [技术选型](02-tech-stack.md#celery-确认与期限基线)。候选与 Judge 的调用上限各 25 秒，覆盖每阶段外部 I/O，Agent 全图时间计入候选阶段的上限。发布租约独立配置，并大于单次 broker 发布期限。

- 正常消息只有在业务写回、登记退避或确认消息为重复后返回。已终态、旧 dispatch_seq、已有有效租约或未到期直接返回。业务状态以 PostgreSQL 为准。
- 子进程异常退出时，启用 late ack 仍可能由父进程确认消息。基线采用 `task_reject_on_worker_lost=False`，由 Dispatcher 按数据库时间回收已领取的租约，并扫描已派发但未领取的项。主进程或主机失联时 Redis 也可能超时重投，幂等校验拦截重复执行。
- 初始每项最多 3 次有效领取（含首次），每项独立限制发布尝试和丢失唤醒的重派次数，起点分别为单事件 5 次和单项 3 次。连续 broker 故障耗尽发布预算或持续静默耗尽重派预算时，记录 `DISPATCH_EXHAUSTED`，落失败并告警。重派只针对已派发且过了冷却仍未领取的到期项，冷却须结合正常排队时间在预发验证。一次事件内发布重试使用相同序号。软超时尽量结清尝试，硬超时走租约回收。业务退避由数据库管理。 [Celery 任务确认](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
- 发件箱耗尽后，由恢复事务按锁序核对当前 dispatch_seq 和执行状态，只有当前事件对应的到期未领取项才能因派发耗尽转为 failed。已领取、已终止或已换序号的项维持原状态。取消请求已受理时按取消规则收敛。


## 状态与取消

| 对象 | 转换与约束 |
| --- | --- |
| 执行项 | `queued → running`，暂态故障 `running → retry_wait → running`。终态为 `succeeded / failed / cancelled`，终态不可逆 |
| 运行 | `queued → running`，接受取消后 `queued/running → cancel_requested`。所有项终止后，按取消已接受 → `cancelled`；全部成功 → `succeeded`；成功和失败并存 → `partial_failed`；全部失败 → `failed` |
| 取消 | API 与领取/完成事务通过运行行串行化。待处理项由调度器标记取消，在途执行于阶段边界停止。取消先提交时，晚到写回只能结束为取消，保留已提交的工件及之前成功的结果。已取消运行返回 200，其他终态返回 409 |

质量低分属于成功完成评分。候选或裁判在必需阶段最终失败属于执行失败。Agent 到达步数限制时，如果有可核验的终止证据，可记录为成功评分、业务未完成。

## 失败与恢复

| 故障 | 行为 |
| --- | --- |
| PostgreSQL 提交后 Redis 暂不可用 | 已受理运行保持排队，恢复后从发件箱派发，并告警积压年龄 |
| 发布成功但未标记、Redis 重投 | 允许重复消息，由执行项领取锁、令牌和结果唯一键去重 |
| 消息已发送后 Redis 数据丢失 | PostgreSQL 扫描未领取项并重新派发 |
| Worker 被终止或租约过期 | 关闭旧尝试，尝试额度内重新派发；审计区分供应商调用结果未知 |
| 裁判失败 | 已提交的候选工件与工具轨迹跨重试复用，裁判计入独立错误分类 |
| 调用供应商成功后进程崩溃，工件尚未提交 | 可能出现重复第三方请求和计费。供应商支持幂等时使用稳定请求 ID，未知消耗单独统计 |

建议每项最多 3 次尝试（含首次），带抖动退避。重试网络暂态、超时、受预算约束的 429 和选定的 5xx；鉴权、无效配置和确定性契约错误直接终止。预算、熔断和背压的参数在预发演练后固定。

## 权限和可观测性

API 对每个资源执行服务端权限检查。配置只接受受控的服务端 `provider_endpoint_ref`，凭据从服务端注入，不经前端传递。管理员操作写入审计表。真实模型 Endpoint 的运行约束见下节。Agent 工具限于代码注册、确定性、只读夹具。候选输入和回答作为数据处理，统一脱敏。日志可按 `request_id`、`run_id`、`item_id`、`attempt_no` 查询，指标使用受控枚举标签。

## Endpoint 与运行预算

- 真实 Endpoint 由部署配置解析为预定义 HTTPS 主机、端口和路径，用户运行请求与候选输出均不能提供 URL。禁用自动重定向和未配置代理。只允许精确匹配白名单主机，校验解析得到的所有地址，拒绝 loopback、link-local、私网、metadata 地址及其他非全局地址。校验后的目标需与实际连接绑定，由受控出口网关强制白名单及地址规则，阻止 DNS 重绑定。若企业批准内部 Endpoint，须通过单独配置的私有出口，精确列明主机和地址范围，仍禁止 metadata、loopback 与 link-local。
- 为外部请求显式配置连接、读取和总时间上限。日志使用允许列表，屏蔽 Authorization、凭据、敏感 URL 参数及原始输入输出。审计只写操作者、动作与资源元数据。
- M3 在外部调用前锁定运行、核验执行项有效租约，并原子写入 `usage_records` 预留一笔 Token 和费用上限、占一个调用名额。服务端为真实模式配置每 Run 的 Token、费用和次数上限，客户端可进一步降低额度。预留上限以可核验的输入 Token 上界、输出 Token 上限、价格和币种计算，无法计算保守上限时拒绝真实调用。
- 候选、Judge、模型驱动的 Agent 步骤、重试及一次格式修复均占预算；禁用 SDK 隐式自动重试。调用完成按已知用量结算，未知消耗保留原预留并标记 `unknown`。若供应商实报超出预留，记录实际用量与告警，并阻止进一步调用。可用余额不足时该执行项失败为 `RUN_BUDGET_EXCEEDED`，已完成结果保留。并行预算与控制事务见 [数据库设计](04-database-design.md#用量与预算)。
