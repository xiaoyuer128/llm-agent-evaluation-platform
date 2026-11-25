# 05 API 接口设计

状态 **设计基线 v0.2** · JSON API

## 约定

业务接口前缀为 `/api/v1`，请求认证用 `Authorization: Bearer <token>`。生产采用经确认的身份源，本地测试可注入角色。JSON 时间用 UTC，ID 用 UUID，缺失指标用 `null`。响应携带 `X-Request-ID`。列表默认 50 条、最多 200 条，返回 `items` 和 `next_cursor`。游标绑定查询条件，以稳定排序键加 ID 做键集分页。运行列表按 `created_at,id` 倒序，样本列表按 `id` 升序。

## 路由

| 方法与路径 | 权限 | 正常返回 | 用途和边界 | 阶段 |
| --- | --- | --- | --- | --- |
| GET /health/live | 探针 | 200 | 进程存活 | M1 |
| GET /health/ready | 探针 | 200 | PostgreSQL 与 schema 可用。M2 起 Redis 异常时仍可受理运行并记录积压 | M1 |
| GET /api/v1/model-configs | viewer | 200 | 按 kind、可用状态查询元数据 | M1 |
| POST /api/v1/model-configs | admin | 201 | 创建不可变 revision，端点仅接受受控引用 | M1 |
| POST /api/v1/model-configs/{config_id}:disable | admin | 200 | 停止用于新运行，保留历史配置和证据 | M1 |
| GET /api/v1/evaluation-profiles | viewer | 200 | 按 kind、可用状态列出版本化评分配置 | M2 |
| POST /api/v1/evaluation-profiles | admin | 201 | 固定规则、权重、裁判版本和 rubric | M2 |
| POST /api/v1/evaluation-profiles/{profile_id}:disable | admin | 200 | 停止用于新运行，保留历史配置和证据 | M2 |
| POST /api/v1/datasets | operator | 201 | 创建数据集，必填 name、kind | M1 |
| GET /api/v1/datasets | viewer | 200 | 支持 kind、archived 过滤 | M1 |
| GET /api/v1/datasets/{dataset_id}/versions | viewer | 200 | 读版本摘要、样本数和校验和 | M1 |
| POST /api/v1/datasets/{dataset_id}/versions | operator | 201 | 创建空草稿，服务端分配 version_no | M1 |
| POST /api/v1/datasets/{dataset_id}/versions/{version_id}/items:import | operator | 200 | application/x-ndjson 导入；dry_run=true 时仅校验 | M1 |
| POST /api/v1/datasets/{dataset_id}/versions/{version_id}:publish | operator | 200 | 发布非空草稿；重复发布返回同一版本摘要 | M1 |
| POST /api/v1/datasets/{dataset_id}:archive | operator | 200 | 停止写入和新运行，保留历史 | M1 |
| POST /api/v1/runs | operator | 202 | 创建批量评测，必须提供 Idempotency-Key | M2 |
| GET /api/v1/runs | viewer | 200 | 支持 status、dataset_version_id 过滤 | M2 |
| GET /api/v1/runs/{run_id} | viewer | 200 | 返回运行、计数与候选级摘要 | M2 |
| POST /api/v1/runs/{run_id}:cancel | operator | 202 | 首次接受或取消处理中返回 202；已取消返回 200，其他终态返回 409 | M2 |
| GET /api/v1/runs/{run_id}/items | operator | 200 | 支持 target_id、status、error_code 过滤 | M2 |
| GET /api/v1/runs/{run_id}/items/{item_id} | operator | 200 | 样本输入摘要、候选工件、评分证据和尝试摘要 | M2 |
| GET /api/v1/runs/{run_id}/comparison | operator | 200 | 同 Run 多候选按样本对照 | M5 |
| POST /api/v1/runs/{run_id}/regression-reports | operator | 201 / 200 | 生成有限规模的 P1 报告，同参数重放返回已存报告 | M6 |
| GET /api/v1/runs/{run_id}/regression-reports/{report_id} | operator | 200 | 查询报告、差异和证据 | M6 |
| GET /api/v1/runs/{run_id}/items/{item_id}/tool-calls | operator | 200 | 默认返回已提交工件轨迹，可按 attempt_no 筛选；按 ordinal 顺序分页 | M4 |

M1 仅实现标为 M1 的路由，运行及其下属路由从 M2 按此契约直接注册，M1 的 Fake 评测通过领域服务和脚本执行。M3 扩展真实供应商、Judge 与预算字段。父资源和子资源一起校验。不存在或跨资源归属的 ID 返回 404。评价配置的量纲和算法由版本管理。

## 通用 Schema

JSON 对象拒绝额外字段，字符串空白规范和大小上限由各 Schema 固定。除标为可空的字段外拒绝显式 null。所有时间为 RFC 3339 UTC，所有 ID（包括路径参数）为 UUID。

列表请求接收 Query `limit: int=50`（1–200）与 `cursor?: string`。响应 `Page<T>={items: T[], next_cursor: string|null}`。游标为带版本的签名不透明字符串，包含资源范围、主体、排序键及规范化过滤条件，不包含敏感内容。无效、篡改或过滤条件变化返回 400 `INVALID_CURSOR`。对可变过滤条件，分页仅保证稳定数据下的确定顺序。运行按 `(created_at,id)` 降序，版本按 `version_no` 降序，样本按 `item_id` 升序，其余元数据按 `(created_at,id)` 降序。轨迹页只使用一个尝试，按 ordinal 升序。

探针响应 `{status: "ok"}` 或 `{status: "ready"}`，未就绪返回 503 错误。M1 本地/测试接入真实应用的受控开发身份或测试依赖注入，生产身份和授权按部署设计配置。

列表通用过滤为 `kind?: Kind`，适用于数据集、模型配置和评分配置。数据集另有 `archived?: bool`，配置另有 `enabled?: bool`，省略时返回所有状态。运行列表过滤为 `status?: RunStatus, dataset_version_id?: UUID`。过滤字段有类型或取值错误时返回 422。

## 管理请求字段

| 资源 | 创建请求 | 成功响应关键字段 |
| --- | --- | --- |
| ModelConfig | `config_key: string, kind: Kind, adapter_key: string, provider: string, model_name: string, provider_endpoint_ref: string, parameters: object={}` | `id: UUID, config_key: string, revision: int, kind: Kind, provider: string, model_name: string, disabled_at: datetime\|null` |
| EvaluationProfile | `profile_key: string, kind: Kind, evaluator_config: object, judge_model_config_id?: UUID\|null=null` | `id: UUID, profile_key: string, revision: int, kind: Kind, disabled_at: datetime\|null` |
| Dataset | `name: string`（去首尾空白后 1–120 字符），`kind: Kind`，`description: string=""`（最多 2,000 字符） | `id: UUID, name: string, kind: Kind, description: string, created_at: datetime, archived_at: datetime\|null` |
| DatasetVersion | 可省略 Body，否则为空对象。沿用路径中的 dataset_id | `id: UUID, dataset_id: UUID, version_no: int, status: draft\|published, item_count: int, checksum: string\|null, published_at: datetime\|null` |

Kind 枚举为 llm/agent。M1 仅接受 llm 和注册的 fake 适配器。创建与禁用返回对应资源，归档返回 Dataset，发布返回 DatasetVersion。路由上的管理动作接受空 Body，重复禁用、归档或发布返回已生效的资源。创建/列出的字段类型与响应 Schema 一致。配置键为 1–64 位 ASCII 字母、数字、下划线或连字符，其他标识为 1–128 字符。配置 revision 由服务端按键编号，创建新 revision 保留原记录。服务端按适配器验证 parameters，评分配置含规则、维度、权重和版本引用。需要裁判时裁判配置必须支持结构化判分。借用已禁用配置提交新运行返回 409，原请求的幂等重放仍有效。配置接口拒绝 URL、API Key 和 `credential_ref`。真实模式根据 `provider_endpoint_ref` 从服务端配置解析凭据引用。M1 的 fake `adapter_key`、`provider` 和 `provider_endpoint_ref` 固定为 `fake`，`model_name` 为注册的夹具名，`parameters` 由适配器 schema 验证。M2 评分配置按规则名称和正权重校验，M3 支持 Judge。未批准的端点引用返回 422 `ENDPOINT_NOT_ALLOWED`。

## 导入契约

Query 参数 `dry_run: bool=false` 显式放在 URL，例如 `?dry_run=true`。Body 是 `application/x-ndjson` 原始字节流，且只能提交 UTF-8，至少一行合法样本。每行至少含 `case_key`、`input`、`expectation`，可带 `tags`。每行 `case_key: string` 为 1–128 字符，`input: object` 与 `expectation: object` 必填，`tags: string[]=[]` 最多 20 个，每项 1–64 字符。样本键在版本内唯一，错误行号从 1 开始。仅草稿允许导入和预览。数据种类决定载荷 schema。输入以 UTF-8 计大小，支持最多 1,000 行和 5 MiB。验证整个批次后追加，重复 case_key 或任一无效行使整批失败。按行返回错误索引。预览不占用顺序号，正式导入重新校验。

```json
{"case_key":"faq-001","input":{"question":"订单取消后可以恢复吗？"},"expectation":{"accepted_answers":["不能恢复"]},"tags":["faq","zh"]}
```

LLM 样本的 `input.question` 必填，`expectation` 按规则类型存储可接受答案或参考值，可补开放题参考答案。Agent 样本的 `input` 含 `task` 与 `fixture_key`，`expectation` 含 `completion_assertions` 与 `allowed_tool_paths`。断言与参数匹配使用受支持的结构化操作符。M1 的规则期望允许 `accepted_answers: string[]`（非空）、`numeric: {value: decimal string, abs_tol?: decimal string, rel_tol?: decimal string}` 或 `json: {value: JSON}`，至少一种。数值由 Decimal 严格校验，容差默认 "0" 且非负。开放题参考答案从 M3 起生效。导入返回 `{version_id: UUID, dry_run: bool, valid_count: int, inserted_count: int, item_count: int}`，dry_run 时 `inserted_count=0`，`item_count` 为当前持久化数量。

`input.question` 为非空字符串，字段大小由导入总限额约束。接受 LF 和 CRLF，允许最后一个换行符。空行和 UTF‑8 解码错误按导入校验失败处理。发布空草稿返回 422 `DATASET_VERSION_EMPTY`，已归档数据集上的新建、导入、发布返回 409 `DATASET_ARCHIVED`。已经发布的版本重放发布仍返回原摘要。

## 提交运行

| Request 字段 | 类型和默认 | 校验 |
| --- | --- | --- |
| dataset_version_id | UUID，必填 | 已发布、数据集未归档 |
| model_config_ids | UUID[]，必填 | 1–5 个互异且启用，保留顺序 |
| evaluation_profile_id | UUID，必填 | 启用且匹配数据种类 |
| repeat_count | int，默认 1 | 1–3，执行项总数最多 1,000 |
| baseline_run_id | UUID 或 null，默认 null | 可读取的终态运行，M6 检查配对资格 |
| token_budget | int 或 null，默认 null，M3 | 提供时为正整数且不超过服务端额度 |
| cost_budget | decimal string 或 null，默认 null，M3 | 大于 0，最多 8 位小数，币种由服务端确定，不能超过服务端额度 |
| max_calls | int 或 null，默认 null，M3 | 提供时为正整数且不超过服务端额度 |

预算字段在 M2 仅接受省略或 null。M3 的 null 表示应用服务端默认上限。非空预算和默认值规范化后参与幂等摘要，服务端解析出的有效预算与币种持久化。计算摘要使用请求中的 null，避免服务端默认配置变化破坏原重放。

成功响应 `{id: UUID, status: "queued", total_items: int}`。例如

`POST /api/v1/runs`，请求头 `Idempotency-Key: model-regression-001`。

```json
{
  "dataset_version_id": "11111111-1111-4111-8111-111111111111",
  "model_config_ids": ["22222222-2222-4222-8222-222222222222"],
  "evaluation_profile_id": "33333333-3333-4333-8333-333333333333",
  "repeat_count": 1
}
```

```http
HTTP/1.1 202 Accepted
Location: /api/v1/runs/44444444-4444-4444-8444-444444444444
Content-Type: application/json

{"id":"44444444-4444-4444-8444-444444444444","status":"queued","total_items":50}
```

候选列表 1–5 个、重复次数 1–3，配置种类须匹配。总执行项数上限见 PRD。可选 `baseline_run_id` 指定已终止基线，对比资格由评测口径决定。幂等键按调用主体隔离，建议使用单次业务意图生成的随机 UUID，长度 1–128 字符。服务端对解析并规范化后的请求（含缺省值）哈希，候选顺序保留。同键同摘要重放返回原 202 和 Location，即便运行已进入终态。权限必须重新校验，已接受请求的配额和配置可用性不重新校验。同键异摘要返回 409。事务失败时可重试原键。并发竞争使用唯一键和短事务解决，并读取胜出事务的摘要与运行标识。运行详情以 GET 返回最新状态。

## 运行结果与对比

RunStatus 为 queued、running、cancel_requested、succeeded、partial_failed、failed、cancelled。ItemStatus 为 queued、running、retry_wait、succeeded、failed、cancelled。取消响应 `{id: UUID, status: RunStatus}`。父子不匹配返回 404。列表和详情都携带 `id, dataset_version_id, evaluation_profile_id, status, total_items, created_at`，时间字段为 UTC；详情可加 `started_at, finished_at`（可空）。`counts` 每个 ItemStatus 的值为非负整数。

详情返回 `status`、`total_items`、`counts`（六项执行状态计数之和等于总数）、`targets`。每个 target 返回 `target_id`、`model_config_id`、`counts`、`metrics`。指标采用 `{value,numerator,denominator}` 表示比例，分位数及用量采用 `{value,sample_count,eligible_count}`，并返回覆盖率或由字段推导。进行中的统计标记 `provisional: true`。

样本列表字段见下节，分值为 0–1 数值或 null。样本详情扩展 `input_summary: object, candidate: object|null, result: object|null, attempts: object[]`。result 为终态评分时含 `outcome: ItemStatus, final_score, rule_score, judge_score, task_completed: bool|null, error_code: string|null, evidence: object`。尝试摘要含 `attempt_no: int, stage: string, outcome: string|null, started_at: datetime, ended_at: datetime|null`。M3 增加 `usage_by_stage`，按 candidate/judge 及重试展示覆盖率、费用和错误，费用为 decimal string 或 null。

对比接口限定同一 Run，Query `target_ids` 为重复出现的 2–5 个唯一 UUID（保留顺序并参与游标签名），再接受 `limit/cursor`。返回 `Page<ComparisonRow>`，每行含 `case_key: string, repeat_no: int, values: ComparisonCell[]`。Cell 含 `target_id: UUID, item_id: UUID, status: ItemStatus, final_score: number|null, latency_ms: int|null, total_tokens: int|null, error_code: string|null`。按 `case_key,repeat_no` 升序，活跃运行允许返回部分结果。明细链接使用 `item_id`。基线跨 Run 比较走 M6 报告。

P1 生成报告请求 `{target_id: UUID, baseline_run_id: UUID, baseline_target_id: UUID, policy_revision: string}`，字段均必填，无默认值。策略版本未登记返回 422 `POLICY_VERSION_UNKNOWN`。两个 Run 必须终态，否则 409 `RUN_NOT_TERMINAL`。生成同步、沿用执行项限额，以候选、基线和策略版本唯一约束，重复生成返回 200。响应含 `id: UUID, run_id: UUID, target_id: UUID, baseline_run_id: UUID, baseline_target_id: UUID, policy_revision: string, status: ReportStatus, reason_code: string|null, metrics: object, evidence_item_ids: UUID[], created_at: datetime`。ReportStatus 为 passed、failed、insufficient_evidence。覆盖不足、数据版本或评分口径不兼容时返回 insufficient_evidence 和原因。查询返回同一 Schema。

## 管理台对接

- 评测提交成功后使用响应的 `Location` 打开运行详情。提交结果未知并重试时复用同一个 `Idempotency-Key`，仅在用户发起新的运行时生成新键。
- 运行详情对 `queued`、`running` 和 `cancel_requested` 轮询，等待上一次请求结束后再发起下一次。进入终态或离开页面时停止自动轮询。遇到 429 按 `Retry-After` 退避，遇到暂时性错误保留上次结果并提示更新时间。
- 样本列表返回 `item_id, target_id, case_key, repeat_no, status, final_score, error_code`，支持按候选、状态和错误类别过滤。对比视图按 `case_key` 和 `repeat_no` 对齐同一数据版本中的各候选，从详情加载候选输出和评分证据，分别显示低分、执行失败和缺失用量。角色受限时展示服务端允许返回的摘要。

## 错误格式

```json
{
  "error": {
    "code": "DATASET_IMPORT_INVALID",
    "message": "导入校验失败",
    "request_id": "req-123",
    "details": [{"line": 2, "field": "case_key", "reason": "duplicate"}]
  }
}
```

| HTTP | 代表错误码 |
| --- | --- |
| 400 | INVALID_CURSOR、INVALID_IDEMPOTENCY_KEY |
| 401 | AUTH_REQUIRED、INVALID_TOKEN |
| 403 | FORBIDDEN |
| 404 | RESOURCE_NOT_FOUND |
| 409 | IDEMPOTENCY_CONFLICT、VERSION_IMMUTABLE、RUN_TERMINAL、RUN_NOT_TERMINAL、CONFIG_DISABLED、DATASET_NAME_CONFLICT、DATASET_ARCHIVED |
| 413 | PAYLOAD_TOO_LARGE |
| 415 | UNSUPPORTED_MEDIA_TYPE |
| 422 | CONFIG_KIND_MISMATCH、DATASET_IMPORT_INVALID、DATASET_VERSION_EMPTY、RUN_LIMIT_EXCEEDED、RUN_BUDGET_INVALID、ENDPOINT_NOT_ALLOWED、POLICY_VERSION_UNKNOWN、VALIDATION_ERROR |
| 429 | RATE_LIMITED，附 Retry-After |
| 503 | DATABASE_UNAVAILABLE、SERVICE_NOT_READY |

执行失败码在结果中返回，例如 `DISPATCH_EXHAUSTED`、`RUN_BUDGET_EXCEEDED`、`JUDGE_INVALID_OUTPUT`，已受理运行通过结果查询获取这些错误。

OpenAPI 在应用阶段由 FastAPI 生成，契约测试检查状态码、响应结构和示例字段。
