# LLM / Agent 自动化评测平台

面向模型研发、Agent 研发和 QA 的内部回归评测平台。使用固定版本的数据集和评分规则，批量执行模型或 Agent，查询可追溯的样本结果与运行指标，为模型替换、提示词升级和 Agent 变更提供发布证据。

当前发布为 **设计基线 v0.1**。仓库包含设计、测试规格及架构图。应用实现、环境文件和运行验收按 [开发进度](docs/09-development-progress.md) 交付。

技术基线为 Vue 3、FastAPI、PostgreSQL 和 Redis + Celery。首期由 Celery 按样本执行项并行。实测确认 Worker 主要等待模型 I/O 后，再引入 `asyncio` 提高单进程并发。

## 文档

| 文档 | 内容 |
| --- | --- |
| [01 项目需求](docs/01-requirements.md) | 用户、范围、流程与验收 |
| [02 技术选型](docs/02-tech-stack.md) | 前后端技术栈、并发策略和开源项目参考 |
| [03 系统架构](docs/03-architecture.md) | 进程、队列、恢复、权限和故障路径 |
| [04 数据库设计](docs/04-database-design.md) | ER 图、表结构、约束、索引与迁移 |
| [05 API 接口设计](docs/05-api-design.md) | 路由、请求响应、状态码与幂等 |
| [06 测试计划与用例](docs/06-test-plan.md) | 分层策略、可复现用例与发布门禁 |
| [07 AI 评测方案](docs/07-evaluation-design.md) | 评分、轨迹、指标、裁判校准与回归判定 |
| [08 部署与运维](docs/08-deployment.md) | 环境、配置、发布、监控与恢复 |
| [09 开发进度](docs/09-development-progress.md) | 已完成交付、里程碑与决策清单 |

## 目标结构

```text
docs/                    当前设计文档
  diagrams/              架构、ER 和评测流程图
backend/                 M1 起交付的 Python 应用
frontend/                M1 起交付的 Vue 3 管理台
tests/                   M1 起交付的自动化测试
scripts/                 随里程碑交付的运维与冒烟脚本
docker-compose.yml       M1 起交付的本地和 CI 环境
Dockerfile               M1 起交付的应用镜像
.env.example             M1 起交付的配置模板
```
