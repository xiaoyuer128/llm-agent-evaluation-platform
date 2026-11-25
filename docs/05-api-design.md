# 05 API 接口设计

状态 **设计基线 v0.1** · JSON API

## 约定

业务接口前缀为 `/api/v1`，请求认证用 `Authorization: Bearer <token>`。生产采用经确认的身份源，本地测试可注入角色。JSON 时间用 UTC，ID 用 UUID，缺失指标用 `null`。响应携带 `X-Request-ID`。列表默认 50 条、最多 200 条，返回 `items` 和 `next_cursor`。游标绑定查询条件，以稳定排序键加 ID 做键集分页。运行列表按 `created_at,id` 倒序，样本列表按 `id` 升序。

## 路由

| 方法与路径 | 权限 | 正常返回 | 用途和边界 |
| --- | --- | --- | --- |
| GET /health/live | 探针 | 200 | 进程存活 |
| GET /health/ready | 探针 | 200 | PostgreSQL 与 schema 可用。Redis 异常时继续受理并发出积压告警 |
| GET /api/v1/model-configs | viewer | 200 | 按 kind、可用状态查询元数据 |
| POST /api/v1/model-configs | admin | 201 | 创建不可变 revision，端点和密钥仅接受受控引用 |
| POST /api/v1/model-configs/{config_id}:disable | admin | 200 | 停止用于新运行，保留历史配置和证据 |
| GET /api/v1/evaluation-profiles | viewer | 200 | 按 kind、可用状态列出版本化评分配置 |
| POST /api/v1/evaluation-profiles | admin | 201 | 固定规则、权重、裁判版本和 rubric |
| POST /api/v1/evaluation-profiles/{profile_id}:disable | admin | 200 | 停止用于新运行，保留历史配置和证据 |
| POST /api/v1/datasets | operator | 201 | 创建数据集，必填 name、kind |
| GET /api/v1/datasets | viewer | 200 | 支持 kind、archived 过滤 |
| GET /api/v1/datasets/{dataset_id}/versions | viewer | 200 | 读版本摘要、样本数和校验和 |
| POST /api/v1/datasets/{dataset_id}/versions | operator | 201 | 创建空草稿，服务端分配 version_no |
| POST /api/v1/datasets/{dataset_id}/versions/{version_id}/items:import | operator | 200 | application/x-ndjson 导入；dry_run=true 时仅校验 |
| POST /api/v1/datasets/{dataset_id}/versions/{version_id}:publish | operator | 200 | 发布非空草稿；重复发布返回同一版本摘要 |
| POST /api/v1/datasets/{dataset_id}:archive | operator | 200 | 停止新运行，保留历史 |
| POST /api/v1/runs | operator | 202 | 创建批量评测，必须提供 Idempotency-Key |
| GET /api/v1/runs | viewer | 200 | 支持 status、dataset_version_id 过滤 |
| GET /api/v1/runs/{run_id} | viewer | 200 | 返回运行、计数与候选级摘要 |
| POST /api/v1/runs/{run_id}:cancel | operator | 202 | 首次接受或取消处理中返回 202；已取消返回 200，其他终态返回 409 |
| GET /api/v1/runs/{run_id}/items | operator | 200 | 支持 target_id、status、error_code 过滤 |
| GET /api/v1/runs/{run_id}/items/{item_id} | operator | 200 | 样本输入摘要、候选工件、评分证据和尝试摘要 |
| GET /api/v1/runs/{run_id}/items/{item_id}/tool-calls | operator | 200 | 默认返回已提交工件轨迹，可按 attempt_no 筛选；按 ordinal 顺序分页 |

父资源和子资源一起校验。不存在或跨资源归属的 ID 返回 404。评价配置的量纲和算法由版本管理。

## 管理请求字段

| 资源 | 创建请求 | 成功响应关键字段 |
| --- | --- | --- |
| ModelConfig | `config_key, kind, adapter_key, provider, model_name, provider_endpoint_ref, credential_ref?, parameters` | `id, config_key, revision, kind, provider, model_name, disabled_at` |
| EvaluationProfile | `profile_key, kind, evaluator_config, judge_model_config_id?` | `id, profile_key, revision, kind, disabled_at` |
| Dataset | `name, kind, description?` | `id, name, kind, created_at, archived_at` |
| DatasetVersion | 空对象。沿用 URL 中的 dataset_id | `id, dataset_id, version_no, status, item_count, checksum, published_at` |

`kind` 枚举为 llm/agent。配置 revision 由服务端按键编号，创建新 revision 保留原记录。服务端按适配器验证 parameters，评分配置含规则、维度、权重和版本引用。需要裁判时裁判配置必须支持结构化判分。借用已禁用配置提交新运行返回 409，原请求的幂等重放仍有效。配置列表不返回秘密引用。

## 导入契约

每行至少含 `case_key`、`input`、`expectation`，可带 `tags`。样本键在版本内唯一。数据种类决定载荷 schema。输入以 UTF-8 计大小，支持最多 1,000 行和 5 MiB。验证整个批次后追加，重复 case_key 或任一无效行使整批失败。按行返回错误索引。预览不占用顺序号，正式导入重新校验。

```json
{"case_key":"faq-001","input":{"question":"订单取消后可以恢复吗？"},"expectation":{"accepted_answers":["不能恢复"]},"tags":["faq","zh"]}
```

LLM 样本的 `input.question` 必填，`expectation` 按规则类型存储可接受答案或参考值，可补开放题参考答案。Agent 样本的 `input` 含 `task` 与 `fixture_key`，`expectation` 含 `completion_assertions` 与 `allowed_tool_paths`。断言与参数匹配使用受支持的结构化操作符。导入成功返回 `version_id, dry_run, valid_count, inserted_count, item_count`。

## 提交示例

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

候选列表 1–5 个、重复次数 1–3，配置种类须匹配。总执行项数上限见 PRD。可选 `baseline_run_id` 指定已终止基线，对比资格由评测口径决定。幂等键按调用主体隔离，建议使用单次业务意图生成的随机 UUID，长度 1–128 字符。服务端对解析并规范化后的请求（含缺省值）哈希，候选顺序保留。同键同摘要重放返回原 202 响应及 Location，同键异请求返回 409；事务未成功时可用原键重试。运行详情以 GET 返回最新状态。

详情返回 `status`、`total_items`、`counts`（六项执行状态计数之和等于总数）、`targets`。每个 target 返回 `target_id`、`model_config_id`、`counts`、`metrics`。指标采用 `{value,numerator,denominator}` 表示比例，分位数及用量采用 `{value,sample_count,eligible_count}`，并返回覆盖率或由字段推导。进行中的统计标记 `provisional: true`。

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
| 409 | IDEMPOTENCY_CONFLICT、VERSION_IMMUTABLE、RUN_TERMINAL、CONFIG_DISABLED |
| 413 | PAYLOAD_TOO_LARGE |
| 415 | UNSUPPORTED_MEDIA_TYPE |
| 422 | CONFIG_KIND_MISMATCH、DATASET_IMPORT_INVALID、RUN_LIMIT_EXCEEDED、VALIDATION_ERROR |
| 429 | RATE_LIMITED，附 Retry-After |
| 503 | DATABASE_UNAVAILABLE、SERVICE_NOT_READY |

OpenAPI 在应用阶段由 FastAPI 生成，契约测试检查状态码、响应结构和示例字段。
