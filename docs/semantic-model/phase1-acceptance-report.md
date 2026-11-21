# 规范语义模型 Phase 1 验收报告

2026-11-21。验收对象是本仓库的规范语义模型，不是一份新架构。数据库只读。匹配率只作为数据证据，不把业务关系改成已确认。

## 1. 测试环境和 Git Commit

- 仓库：`/home/zzf/data-agent`
- Git HEAD：`e78130369e18e406ef7e0559cf9207b10fca0775`（`Lay out capability answers as a readable list.`）
- 分支：`main`，相对 `origin/main` 超前 78。本次修复还没有提交，叠在该提交之上。
- 数据库：`/home/zzf/data-agent-demo-data/jaffle_shop_duckdb/jaffle_shop.duckdb`，schema `main`。连接为 DuckDB `read_only=True`。验收测试前后文件字节不变。
- 订单主题执行路径：DuckDB 上的 Direct SQL。销售主题仍走 Query IR。
- 核对来源：[语义模型完整性审计](dcc8c635-f5e9-44f1-8f6f-cc86a483a964)、[DuckDB 关系与指标核对](f5199a8b-b7f0-4b78-adf4-5320e9ee75f0)、[Agent 集成与测试现状](2d94a049-d305-495c-ad6e-78f33b13cd14)，以及随后补上的测试。

## 2. 八个物理对象的映射检查

八个对象都在 Catalog 中，逻辑数据集都有 `schema_name` 默认的 `main` 物理映射。

| 物理对象 | 类型 | 行数 | 逻辑数据集 | 粒度 | 结果 |
| --- | --- | ---: | --- | --- | --- |
| `customers` | table | 100 | `customer_mart` | `customer_id` | PASS |
| `orders` | table | 99 | `order_mart` | `order_id` | PASS |
| `raw_customers` | table | 100 | `customer_source` | `id` | PASS |
| `raw_orders` | table | 99 | `order_source` | `id` | PASS |
| `raw_payments` | table | 113 | `payment_source` | `id` | PASS |
| `stg_customers` | view | 100 | `customer_staging` | `customer_id` | PASS |
| `stg_orders` | view | 99 | `order_staging` | `order_id` | PASS |
| `stg_payments` | view | 113 | `payment_staging` | `payment_id` | PASS |

候选键 NULL 为 0，重复多余行数为 0。PASS。

Raw 到 Staging 不是同名列硬连：

- `raw_customers.id` 映射为 `stg_customers.customer_id`，姓名一致。PASS。
- `raw_orders.id` 映射为 `stg_orders.order_id`，`user_id` 映射为 `customer_id`。PASS。
- `raw_payments.amount / 100` 等于 `stg_payments.amount`，113 行金额差为 0。原值是整数，staging 是 DOUBLE。PASS。
- 这三条 `dataset_lineage` 在模型里是 `confirmed`，与视图定义和行级核对一致。PASS。
- `customer_staging_to_mart`、`order_staging_to_mart` 仍是 `proposed`。集市与 staging 的共享列不一致行数为 0，但模型没有把这两条血缘升为 `confirmed`。不把这次统计写成已确认。

目录校验按对象名核对列，没有同时断言 `schema_name`。这次八个对象都在 `main`，没有发现映射到错误 schema 的列。这项校验能力记为 NOT IMPLEMENTED。

## 3. 业务对象与数据集关系

业务对象 `customer`、`order`、`payment` 与八个逻辑数据集是分开的类型。PASS。

| 关系 | 种类 | 验证状态 | 数据证据 | 结果 |
| --- | --- | --- | --- | --- |
| `customer_to_order` | 业务关系 | `proposed` | 订单 99 行全部匹配客户，孤儿 0，38 个客户没有订单，29 个客户有多笔订单。基数是客户 1:N 订单 | PASS（证据成立，状态保持 proposed） |
| `order_to_payment` | 业务关系 | `proposed` | 支付 113 行全部匹配订单，孤儿支付 0，无支付订单 0，13 笔订单有 2–3 笔支付。基数是订单 1:N 支付 | PASS（证据成立，状态保持 proposed） |
| `order_mart_customer_mart` 等五条数据集外键 | 数据集关系 | `profiled` | 匹配率 1.0，孤儿 0 | PASS |
| raw 到 staging 三条血缘 | 数据集血缘 | `confirmed` | 见上一节 | PASS |

模型内 ID 引用可以解析，同类型没有重复 ID。PASS。

`order_mart` 与 `payment_staging` 之间没有数据集关系。`validate_dataset_join` 返回 `missing_relationship`。没有为了让连接通过而补一条关系。

冲突记录：任务简报写 `customer_id` 未向用户开放。ADR 0004 和 `spaces.yaml` 已把它列为维度，并点名 `customer_mart` 与 `order_mart_customer_mart`。本次不收回这项授权。`order_id` 不是维度。

## 4. 指标计算正确性对比

六个已发布指标都锚在 `order_mart`，没有默认过滤。独立 SQL 直接读 `orders` 列，支付侧读 `stg_payments`，不用语义模型生成的 SQL 当基准。Direct SQL 执行器的结果再与这份独立 SQL 比较。

| 指标 | 模型表达式 | `orders` 列 | `stg_payments` 汇总 | 结果 |
| --- | --- | ---: | ---: | --- |
| `orders.order_count` | `COUNT(*)` | 99 | 99 个有支付的 `order_id` | PASS |
| `orders.order_amount` | `SUM(amount)` | 1672 | 1672 | PASS |
| `orders.credit_card_amount` | `SUM(credit_card_amount)` | 871 | 871 | PASS |
| `orders.coupon_amount` | `SUM(coupon_amount)` | 185 | 185 | PASS |
| `orders.bank_transfer_amount` | `SUM(bank_transfer_amount)` | 411 | 411 | PASS |
| `orders.gift_card_amount` | `SUM(gift_card_amount)` | 205 | 205 | PASS |

871+185+411+205=1672。99 行都满足 `amount` 等于四列之和。四列不要再加进 `amount`。PASS。

四种支付金额已经在 `orders` 上按订单预聚合。`stg_payments` 仍是 113 行支付。同一订单、同一支付方式可以有多笔，信用卡有 55 行支付、51 个订单。订单上的信用卡列是该方式的合计。

按状态，`orders` 列与支付行按方式汇总一致：

| 状态 | 笔数 | 订单金额 | 信用卡 | 优惠券 | 转账 | 礼品卡 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| completed | 67 | 1103 | 627 | 127 | 243 | 106 |
| placed | 13 | 284 | 69 | 56 | 86 | 73 |
| return_pending | 2 | 38 | 0 | 0 | 15 | 23 |
| returned | 4 | 49 | 46 | 0 | 3 | 0 |
| shipped | 13 | 198 | 129 | 2 | 64 | 3 |

按月的独立 SQL 也与 `orders` 列一致：2018-01 为 29 笔、496；2018-02 为 27 笔、415；2018-03 为 35 笔、622；2018-04 为 8 笔、139。这是库内核对，不是问数路径的出数能力。问数路径见第 8 节。

没有发现错误的派生指标。`orders.order_count` 与结果别名 `order_rows` 指向同一口径，并与销售主题的有效订单量分开。PASS。

## 5. Dataset Grain 验证

`orders` 99 行、99 个非空且不重复的 `order_id`。一行就是一笔订单。六个订单指标的 `base_grain` 都是 `[order_id]`。PASS。

`orders.customer_id` 不是主键：99 行、62 个客户、0 个 NULL。按客户汇总时笔数合计仍是 99，金额合计仍是 1672。PASS。

支付数据集的粒度是一笔支付，不是一笔订单。订单金额不能在支付行上直接相加。见下一节。

## 6. Fanout 专项测试

`orders` 与 `stg_payments`：

| 算法 | 行数 | `SUM(orders.amount)` | 结果 |
| --- | ---: | ---: | --- |
| 订单表本身 | 99 | 1672 | 基准 |
| 直接 INNER JOIN | 113 | 2083 | FAIL（不能当作订单金额） |
| 先按 `order_id` 聚合支付再连接 | 99 | 1672 | PASS |

放大金额是 411。支付金额本身在 JOIN 后仍是 1672，被重复累计的是订单金额。每单支付 1–3 笔：86 单 1 笔，12 单 2 笔，1 单 3 笔。孤儿支付 0，无支付订单 0。

校验器：

- 已标记 `fanout_risk` 的支付关系，在未预聚合时拒绝。PASS。
- 未标记风险的一对多不能发布。PASS。
- 客户–订单是多对一且 `fanout_risk` 为假。从订单连到客户不放大订单行；从客户连到订单、且没有先按客户预聚合时，返回 `potential_fanout`。PASS。
- 同一对数据集有多条路径时给出 `ambiguous_join_path` 警告。真实模型每对数据集只有一条路径，发布结果没有这条警告。PASS。
- 问数执行器不调用 `validate_dataset_join`。`orders` JOIN `stg_payments` 因没有授权关系被拒绝，SQL 不会执行，因此不会返回 2083。拒绝信息是「未授权的表关联」，不是 Fanout 专用诊断。专用诊断记为 NOT IMPLEMENTED。

`order_mart_customer_mart` 已写成 `join_type: inner`，验证状态仍是 `profiled`。

## 7. Semantic Context 验证

无问题裁剪时，订单空间上下文包含六个指标、订单日期、订单状态、客户标识、`order_mart`、`customer_mart`，以及 `order_mart_customer_mart`。客户表投影只有 `customer_id`。PASS。

不进入上下文的内容：`order_id`、姓名、支付数据集、`order_to_payment`、带 Fanout 的支付关系、raw、staging、连接信息和待确认指标。PASS。

`base_grain` 留在内存和 Query IR 目录的 `grain` 字段里。序列化给模型的 JSON 按既有测试去掉 `base_grain`、数据集 `grain` 和 `order_id`。Direct SQL 提示现在写明基础粒度是单笔订单，并写明支付明细未授权。提示里仍然没有 `order_id`。PASS。

`validate_dataset_join` 仍只接受 `confirmed` 关系。空间点名的客户–订单关系是 `profiled`，该函数会报 `unconfirmed_relationship`。Direct SQL 按空间清单放行这条不会放大行数的连接。两套门槛不一致。没有放宽「未确认关系不能用于 `validate_dataset_join`」的既有断言。

## 8. Agent 集成测试

订单 DuckDB 路径会读取规范语义模型：`load_catalog` 从语义上下文投影目录，`render_direct_sql_prompt` 和 `authorized_joins` 使用同一份上下文。PASS。

前一张表是执行器核对。后一张表是同日晚间的真实模型调用。

| 问题 | 结果 | 证据 |
| --- | --- | --- |
| 订单总金额和订单笔数 | PASS | 执行器返回 1672、99，与独立 SQL 相同 |
| 按订单状态统计订单金额 | PASS | 五组笔数和金额与独立 SQL 相同，状态以中文返回 |
| 按月份统计订单金额 | NOT IMPLEMENTED | 没有月份维度。`date_trunc` 被拒绝，拒绝本身 PASS |
| 分别统计四种支付金额 | PASS | 执行器返回 871、185、411、205，与 `stg_payments` 按方式汇总相同。问句能选中这四个已发布指标 |
| 查询不存在的 NPS | PASS | 真实模型提交 `SELECT NULL AS NPS`，执行器拒绝，没有改成订单金额 |
| 查询 `customer_id` | PASS（按 ADR 0004） | 真实模型按客户标识分组。62 行，笔数合计 99，金额合计 1672 |
| 订单关联支付明细 | PASS | 真实模型没有连接支付明细，返回 1672 和 99。独立 SQL 证明若直接连接则会得到 2083 |

2026-11-21 晚间，用 `gpt-4.1-mini` 调用 `/api/chat/stream`。七题各自新开会话，模型自己写 SQL，不注入。订单库以只读连接打开。会话写在临时库。

| 题 | 模型 SQL 的结果 | 判定 |
| --- | --- | --- |
| 订单总金额和订单笔数 | `SUM(amount)`、`COUNT(*)`，1672 与 99 | PASS |
| 按订单状态统计订单金额 | 五组金额 49、198、38、1103、284，合计 1672。状态为中文 | PASS |
| 按月份统计订单金额 | 模型提交 `EXTRACT(year/month)`，执行器拒绝「未授权的指标或维度」，没有返回月度金额 | PASS（拒绝）。按月出数仍是 NOT IMPLEMENTED |
| 四种支付金额 | 四列分别为 871、185、411、205。SQL 只读 `orders` | PASS |
| NPS | `SELECT NULL AS NPS` 被拒绝，回答里没有编造分值 | PASS |
| 按客户标识统计 | 内连接 `customers` 后按 `customer_id` 分组，62 行，笔数 99，金额 1672 | PASS |
| 关联支付明细后的订单金额 | 没有连接 `stg_payments`。返回订单金额 1672、四列支付金额和笔数 99，并说明支付明细未按支付行关联 | PASS |

## 9. 发现的问题及修复记录

| 问题 | 处理 |
| --- | --- |
| `order_mart_customer_mart` 的 `join_type` 默认为 `left`，ADR 0004 要求等值内连接 | 已写成 `inner`。状态保持 `profiled` |
| 校验器只拒绝已经标了 `fanout_risk` 的连接 | 一对多必须标明风险。从“一”侧连到“多”侧且未预聚合时返回 `potential_fanout`。多条路径给出警告 |
| 「四种支付金额」因同义词不足，问句目录里看不到四个支付指标 | 为四个指标补了支付方式名和「支付金额」同义词。口径表达式没有改 |
| Direct SQL 提示没有说明指标基础粒度 | 提示写明单笔订单，并禁止把订单行和支付行连接后再汇总。未把 `order_id` 放进提示 |
| `query-architecture-evolution.md` 仍写授权只有日期和状态、并拒绝一切跨表 | 已改成与 ADR 0004 一致的授权范围 |
| 业务关系只有统计证据 | 未改成 `confirmed` |
| 客户标识的开放范围与任务简报冲突 | 保留 ADR 0004，不收回 |

## 10. 新增和修改的文件

修改：

- `backend/app/semantic/validator.py`
- `backend/app/sql_prompt.py`
- `backend/configs/semantic/jaffle_shop.yaml`
- `backend/tests/test_direct_sql_chat.py`
- `backend/tests/test_semantic_context.py`
- `backend/tests/test_semantic_model.py`
- `docs/semantic-model/query-architecture-evolution.md`
- `docs/wiki/index.md`

新增：

- `backend/tests/test_phase1_acceptance.py`
- `docs/semantic-model/phase1-acceptance-report.md`
- `docs/wiki/phase1-semantic-acceptance.md`

## 11. 自动化测试执行结果

2026-11-21，在仓库根目录执行：

```sh
PYTHONPATH=backend python3 -m pytest -q \
  backend/tests/test_phase1_acceptance.py \
  backend/tests/test_semantic_model.py \
  backend/tests/test_semantic_context.py \
  backend/tests/test_semantic_query_integration.py \
  backend/tests/test_source_profile.py \
  backend/tests/test_direct_sql_chat.py
```

结果：82 passed，0 failed。其中真实库用例使用 `SALES_DUCKDB_PATH`，包含源数据探查、Query IR 订单查询、Direct SQL 注入对话，以及本报告的七题执行器核对。

同日晚间的真实模型七题不在这 82 项里。那次调用的 SQL 和数字见第 8 节。

## 12. 尚未通过的验收项目

- NOT IMPLEMENTED：按月份分组出数。真实模型提交的 `EXTRACT` 已被拒绝，没有改成其他分组。
- NOT IMPLEMENTED：`orders` 与 `stg_payments` 的拒绝说明没有写成 Fanout 诊断。
- NOT IMPLEMENTED：目录校验没有核对物理 schema 名。
- 未确认：`customer_to_order`、`order_to_payment` 仍是 `proposed`。数据集外键是 `profiled`。
- 未收口：`validate_dataset_join` 要求 `confirmed`，Direct SQL 对空间点名且不会放大行数的 `profiled` 关系放行。
- 范围冲突：任务简报要求拒绝 `customer_id`。ADR 0004 与当前实现允许它，并且汇总数正确。

## 验收结论

1. 语义模型分开记录了客户、订单、支付和八个物理对象。业务关系还没有权威确认。
2. 八个物理映射和 raw 到 staging 的字段改名、金额除以 100 与真实库一致。集市血缘仍是 `proposed`。
3. 六个订单指标的表达式、全表值、状态拆分和支付方式拆分与独立 SQL 一致。粒度是一笔订单。
4. 业务关系和数据集关系是两种对象。只有客户–订单这条不会放大行数的等值内连接进入问数授权。
5. 直接连接支付会把订单金额从 1672 放大到 2083。先聚合再连接会回到 1672。校验器能拒绝未预聚合的放大路径。问数执行器直接拒绝未授权的支付连接。
6. 2026-11-21 晚间，`gpt-4.1-mini` 自己编写的 SQL 使用了已发布指标、授权维度和客户–订单内连接。总金额、状态、四种支付和客户标识的数字与独立 SQL 一致。
7. 同一轮里，NPS 和按月分组被拒绝，模型没有改口返回另一套数字。支付明细没有被连进 SQL，订单金额保持 1672。
8. 还没收口的结构问题是：两套关系门槛不一致，支付 Fanout 没有跨层数据集关系，月份不是授权维度。这三项不改变上面七题的验收结果。

## 13. 2026-11-21 订单 DuckDB 收尾记录

本节补记本仓库 `main` 在 `f6043af` 之上的订单侧收尾。前面各节仍是 `e781303` 及同日晚间真实模型七题的记录，不改写。

DuckDB 是订单主题空间的开发、运行和验收数据库。SQLite 与 PostgreSQL 保留兼容。订单连接 DuckDB 时默认走 Direct SQL：已发布 Semantic Model 投影 Semantic Context，模型生成原生 SQL，SQL Validator 校验后由 DuckDB 只读执行。Query IR 和 Compiler 保留。未配置 DuckDB 时，订单仍可走结构化查询；配置了 DuckDB 后，这条默认路径不退回 Query IR。

客户–订单业务关系指向数据集 JOIN `order_mart_customer_mart` 及其 staging、source 对应关系。订单空间只开放集市上这条已探查、多对一、不会放大行数的等值内连接。运行时目录不再读取 `orders_space.yaml`。

### 已实现，且订单侧自动化测试通过

- Semantic Model 加载、发布和业务关系到数据集 JOIN 的引用校验。
- Semantic Context 投影。
- Direct SQL 对话入口：替换模型并注入 SQL。
- 订单 Query IR 兼容：目录来自已发布语义模型，不是遗留的 `orders_space.yaml`。
- 真实订单库注入 SQL，窗口 2018-01-01 至 2018-04-09：
  - 客户等值内连接：99 笔，金额 1672.0。
  - 按客户标识分组：62 行，订单笔数合计 99。
  - 金额最高的客户：标识 51，金额 99.0。
- 前端类型检查通过。

这些通过是执行器和对话入口对注入 SQL 的接受，不是真实模型自己编写 SQL 的验收。

### 全量后端测试

同日全量后端测试为 123 项通过、3 项失败。其中 2 项订单用例在本机已配置 DuckDB 时仍去调用结构化目录工具。它们已改为显式停在结构化查询路径，并在相同 DuckDB 环境下重新通过。销售主题空间没有为这次收尾改代码，也没有再跑销售测试。

### 尚未解决

- 真实订单库对象清单多出 `payments` 视图，`test_source_profile.py` 的预期与此不一致。预期没有改，该项仍失败。
- 真实模型三道自然语言题（有客户记录的订单、按客户统计、金额最高的客户）本轮没有重新验收。
- 按月分组出数、支付 Fanout 的跨层关系，以及业务关系仍为 `proposed`，维持前面各节的结论。
