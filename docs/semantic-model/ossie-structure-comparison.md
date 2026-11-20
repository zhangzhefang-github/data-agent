# Canonical Semantic Model v1 与 Ossie 的结构对照

本文只对照两边已经写进规范或模型契约的结构。不提出代码改动。

## 来源

Ossie 读的是本机检出，不是二手转述。

- 路径：`/home/zzf/data-agent/external/ossie`，符号链接指向 `/home/zzf/lab/ossie`。
- 提交：`e4f545a4fd6aef1a98a8438c22e550d67a992e06`（短哈希 `e4f545a`），说明为 `test(validation): make four reference and file-name tests pass on Windows (#739)`，时间 2026-11-16。
- 远端：`git@github.com:zhangzhefang-github/ossie.git`。
- 规范版本：核心规范与本体规范的文首都写 `0.3.0.dev0`，并标明草案、未发布。`docs/index.md` 写当前开发版为 `0.3.0.dev0`，最近已发布版为 `0.1.1`。

Ossie 本文使用的规范正文：

| 文件 | 用来对照的内容 |
| --- | --- |
| `external/ossie/README.md` | 仓库组成：核心规范、本体、文档各在何处 |
| `external/ossie/docs/index.md` | 规范一览和术语表 |
| `external/ossie/core-spec/spec_layers.md` | 四层分工 |
| `external/ossie/core-spec/spec.md` | 模型、数据集、关系、字段、指标、名字解析 |
| `external/ossie/core-spec/spec.yaml` | 同一套模型字段的机器可读说明 |
| `external/ossie/core-spec/expression_language.md` | 表达式里允许的聚合，以及未支持语法的理由 |
| `external/ossie/core-spec/relational_semantics.md` | Layer 2 如何把模型呈现成关系、度量、行身份和可发现关系 |
| `external/ossie/core-spec/wide_table_semantics.md` | Layer 3 查询里的维度、指标、过滤和关系路径 |
| `external/ossie/ontology/ontology.md` | 概念、本体关系，以及逻辑字段到本体的映射 |
| `external/ossie/ROADMAP.md` | 路线图里写明尚未进入当前模型的交付项 |

`docs/spec_changes.md` 自己写明：它是变更记录，规范行为以 `core-spec/spec.md` 和 `ontology/ontology.md` 为准。本文不把它当作结构定义。

本仓库对照这些文件：

| 文件 | 用来对照的内容 |
| --- | --- |
| `docs/semantic-model/canonical-semantic-model-v1.md` | 四层对象，以及已授权 `confirmed` 关系 |
| `docs/adr/0002-canonical-semantic-model.md` | 模型要统一承载的对象，以及口径与授权这一目的 |
| `GLOSSARY.md` | 主题空间、业务关系、数据集关系、粒度、Fanout、关系验证状态 |
| `backend/app/semantic/models.py` | 类型字段 |
| `backend/configs/semantic/jaffle_shop.yaml` | 这些类型在一份模型里的实例 |

`models.py` 的根对象是 `SemanticModel`。Jaffle Shop 实例有 1 个 `Domain`（`sales`）、3 个业务对象、2 条业务关系、8 个数据集、5 条数据集关系、4 个维度、6 个已发布指标。这与 `canonical-semantic-model-v1.md` 的计数一致。

## 层次对照表

两边都用“层”这个词，分法不同。Canonical Semantic Model v1 的四层是模型内部的对象分组。Ossie 的四层里，Layer 1 才是模型格式；Layer 2 和 Layer 3 是查询接口；Layer 4 是另一份本体文档。

| Canonical Semantic Model v1 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- |
| 业务概念：`Domain`、`BusinessObject`、`BusinessAttribute`、`BusinessRelationship` | `docs/semantic-model/canonical-semantic-model-v1.md`；类型在 `backend/app/semantic/models.py` | Layer 1 把 dataset 说成 business entity。独立的概念模型在 Layer 4：`concept`、`relationships`、`identify_by` | `external/ossie/core-spec/spec_layers.md`；`external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/ontology/ontology.md` |
| 分析语义：`Metric`、`Dimension`、`MetricExpression`、`MetricFilter` | 同上 | Layer 1 的 `metrics` 与字段上的 `dimension`。Layer 3 查询再按 `dimensions`、`metrics`、`filter` 取这些名字 | `external/ossie/core-spec/spec.md` 的 Fields、Metrics；`external/ossie/core-spec/wide_table_semantics.md` |
| 逻辑数据：`Dataset`、`DatasetField`、`DatasetRelationship` | 同上 | Layer 1 的 `datasets`、`fields`、`relationships` | `external/ossie/core-spec/spec.md` |
| 物理映射：`DataSource`、`PhysicalMapping` | 同上 | `dataset.source` 字符串，加上字段 `expression` 对源列的引用。源列本身不在文档里声明 | `external/ossie/core-spec/spec.md` 的 Datasets、Name Resolution |
| 模型独立于 Agent、Query IR、Compiler 和数据库连接 | `docs/semantic-model/canonical-semantic-model-v1.md` | Layer 2、Layer 3 明确写它们定义查询语义，不定义模型格式 | `external/ossie/core-spec/spec_layers.md`；`external/ossie/core-spec/wide_table_semantics.md` 第 1.4 节 |

Ossie 一份 JSON 或 YAML 文档恰好一个语义模型。根上必有 `version`、`name`、非空 `datasets`；可选 `description`、`ai_context`、`relationships`、`metrics`、`nonportable_extensions`。没有 `semantic_model` 包装，也没有跨模型引用或 bundle。见 `external/ossie/core-spec/spec.md` 的 Semantic Model。

本仓库一份 `SemanticModel` 同时装下 `domains`、`data_sources`、`business_objects`、`business_relationships`、`datasets`、`dataset_relationships`、`metrics`、`dimensions`。`version` 是模型自己的版本字符串；Jaffle Shop 写的是 `"1.0"`。见 `backend/app/semantic/models.py` 的 `SemanticModel`，实例见 `backend/configs/semantic/jaffle_shop.yaml`。

### 业务对象

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 分组 | `Domain`：`id`、`name`、`description`。业务对象和数据集用 `domain_id` 指向它。Jaffle Shop 只有 `sales` / 销售 | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 核心模型没有 domain 字段。一份文档的边界就是模型 `name` | `external/ossie/core-spec/spec.md` 的 Semantic Model |
| 业务对象 | `BusinessObject`：`id`、`name`、`domain_id`、`candidate_key`、`attributes`、`dataset_ids`。实例是 `customer`、`order`、`payment`，各自挂到 mart、staging、source 数据集 | 同上 | Layer 1 没有单独的业务对象类型。Datasets 一节写：logical datasets represent business entities or concepts (fact and dimension tables) | `external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/docs/index.md` 术语表 Dataset |
| 业务属性 | `BusinessAttribute`：`id`、`name`、`data_type`、`restricted`。属性列表挂在业务对象上，schema 里没有指向某个 `DatasetField` 的字段 | `backend/app/semantic/models.py` | 本体概念没有并列的属性数组。值由关系的 role 指向 `ValueType` 概念；标识写在 `identify_by` | `external/ossie/ontology/ontology.md` 的 Concepts、Relationships、Identifying relationships |
| 概念类型 | 业务对象没有 entity / value 枚举 | `backend/app/semantic/models.py` | `ConceptType` 为 `EntityType` 或 `ValueType`。`EntityType` 表示必须靠其他信息来引用的现实概念。每个概念有 `concept`、`type`，可选 `extends`、`derived_by`、`identify_by`、`requires`、`relationships`、`iri` | `external/ossie/ontology/ontology.md` 的 Concept types、Concepts |
| 对象与数据集的连接 | `dataset_ids` 直接列出数据集 id | `backend/app/semantic/models.py` | 映射文档用 `semantic_model_ref` 指向一份语义模型，再用 `concept_mappings` / `object_mappings` 把字段表达式映射到概念。映射不内嵌物理表 | `external/ossie/ontology/ontology.md` 的 Mapping documents、Object mappings |

Jaffle Shop 的 `customer.candidate_key` 是单个字符串 `customer_id`。Ossie 数据集的 `primary_key` 是字段名数组；本体的 `identify_by` 是关系名数组。三处都表示“用什么认出一个对象”，载体不同。数据集键见 `external/ossie/core-spec/spec.md` 的 Datasets；本体标识见 `external/ossie/ontology/ontology.md` 的 Identifying relationships；本仓库见 `backend/app/semantic/models.py` 的 `BusinessObject.candidate_key` 与 `Dataset.primary_key`。

`ROADMAP.md` 的 Semantic Modeling 仍把 Explicit entity modeling、Cross-domain modeling support，以及“本体层描述业务概念”列为交付项。当前检出里，`ontology/ontology.md` 已经是 Layer 4 规范，版本同样是 `0.3.0.dev0`。路线图和已落地的本体正文同时存在；本文只记录这一事实。

### 指标

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 放置 | 全部 `Metric` 在模型根的 `metrics`。每条必有一个 `source_dataset_id`。Jaffle Shop 六条订单指标都指向 `order_mart` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 两种位置，结构相同。`datasets[].metrics` 是 dataset-scoped，表达式只解析本数据集。根上 `metrics` 是 model-scoped，用 `dataset.field` 跨数据集 | `external/ossie/core-spec/spec.md` 的 Metrics、Metric Scoping；`external/ossie/docs/index.md` 术语表 Metric |
| 标识 | `id` 与显示名 `name` 分开。规范 ID 示例是 `orders.order_count` | `docs/semantic-model/canonical-semantic-model-v1.md`；`docs/adr/0002-canonical-semantic-model.md`；`backend/configs/semantic/jaffle_shop.yaml` | `name` 就是标识。数据集内指标对外写成 `dataset_name.metric_name`；模型级指标写成 `metric_name` | `external/ossie/core-spec/spec.md` 的 Metric Scoping |
| 表达式 | `MetricExpression.kind`：`sum`、`count`、`count_distinct`、`avg`、`min`、`max`、`ratio`、`metric`。`count` 不接受 `field_id`。`ratio` 指向分子、分母指标，并有 `zero_denominator`：`null` 或 `zero` | `backend/app/semantic/models.py`；`docs/semantic-model/canonical-semantic-model-v1.md` | `expression.sql` 是 `OSSIE_SQL_2026` 文本，可选 `dialects`。聚合函数包括 `SUM`、`COUNT`、`COUNT(*)`、`COUNT(DISTINCT expr)`、`AVG`、`MIN`、`MAX`，以及统计、分位数和近似函数。比例写在 SQL 里，例如 `SUM(...) / COUNT(DISTINCT ...)` | `external/ossie/core-spec/spec.md` 的 Metrics；`external/ossie/core-spec/expression_language.md` 的 Aggregation Functions |
| 指标引用指标 | `kind: metric`，用 `metric_id` | `backend/app/semantic/models.py` | 模型级指标引用数据集级指标的唯一形式是 `AGG(dataset.metric)`。其他引用方式规范写明无效 | `external/ossie/core-spec/spec.md` 的 Metric Scoping 规则 6 |
| 口径说明 | `definition` 字符串。同义词是 `synonyms` 数组 | `backend/app/semantic/models.py` | `description`。同义词放在 `ai_context.synonyms` | `external/ossie/core-spec/spec.md` 的 Metrics、AI Context Structure |
| 粒度与时间 | `base_grain` 必填。可选 `time_dimension_id`。Jaffle Shop 订单指标的 `base_grain` 是 `[order_id]`，时间维度是 `orders.order_date` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml`；`GLOSSARY.md` 的指标基础粒度 | 数据集级指标在该数据集的行级上聚合。Layer 2 把行身份对应到 `primary_key`，并写明行身份是否需要单独声明语法仍是开放问题 | `external/ossie/core-spec/spec.md` 的 Metric Scoping；`external/ossie/core-spec/relational_semantics.md` 第 3.2、3.4 节 |
| 可一起查询的维度 | `compatible_dimension_ids`。维度上还有 `compatible_metric_ids` | `backend/app/semantic/models.py` | 模型字段里没有兼容列表。Layer 3 用关系图判断维度数据集能否从指标的 home dataset 到达 | `external/ossie/core-spec/wide_table_semantics.md` 第 4、5.3 节 |
| 默认过滤 | `MetricFilter`：`dimension_id`、`operator`、`value`，挂在 `default_filters` | `backend/app/semantic/models.py` | 指标 schema 没有 `default_filters`。Layer 3 的 `filter` 是查询的一部分，不是模型字段 | `external/ossie/core-spec/spec.md` 的 Metrics；`external/ossie/core-spec/wide_table_semantics.md` 第 3.2、3.4 节 |
| 其他分析属性 | `additive`、`unit`、`published`。Jaffle Shop 六条订单指标都是 `published: true` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 指标 schema 的其余字段是 `datatype`、`ai_context`、`nonportable_extensions` | `external/ossie/core-spec/spec.md` 的 Metrics |
| 查询时的度量 | 规范写明模型可表达的聚合范围高于当前确定性 Compiler 的子集 | `docs/semantic-model/canonical-semantic-model-v1.md` | Layer 2 把数据集级 metric 呈现为 measure，用 `AGG()` 求值。模型级 metric 不作为 measure 列暴露 | `external/ossie/core-spec/relational_semantics.md` 第 3.4、10.3 节 |

`expression_language.md` 把 `SUM`、`COUNT`、`COUNT(*)`、`COUNT(DISTINCT)`、`MIN`、`MAX` 标成 distributive，把 `AVG` 标成 algebraic，把 `COUNT(DISTINCT)` 标成 holistic。本仓库用 `additive` 布尔值，schema 里没有这组分解类别。两边文件分别是 `external/ossie/core-spec/expression_language.md` 的 Core Aggregation Functions，和 `backend/app/semantic/models.py` 的 `Metric.additive`。

### 维度

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 是不是独立对象 | `Dimension` 是模型根上的列表。字段有 `id`、`name`、`source_dataset_id`、`field_id`、`is_time`、`synonyms`、`compatible_metric_ids`、`user_visible` | `backend/app/semantic/models.py` | 术语表写：dimensions are represented as fields with optional metadata such as `is_time`。模型里它是字段上的可选对象 `dimension`，目前只有 `is_time` | `external/ossie/docs/index.md` 术语表 Dimension；`external/ossie/core-spec/spec.md` 的 Dimension Object；`external/ossie/core-spec/spec.yaml` 的 Fields Schema |
| 和时间类型的关系 | `is_time` 默认 `False`。Jaffle Shop 里 `orders.order_date` 显式为 true；`customers.first_order`、`customers.most_recent_order` 未设置 | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | `datatype` 与 `dimension.is_time` 互相独立。未写 `is_time` 时，`Date`、`Time`、`DateTime`、`DateTimeTz` 默认为 true，其他类型默认为 false。显式值优先 | `external/ossie/core-spec/spec.md` 的 DataType and `is_time` |
| 查询里的维度 | 维度 id 如 `orders.order_date` 指向一个数据集字段 | `backend/configs/semantic/jaffle_shop.yaml` | Layer 3 查询的 `dimensions` 必须是字段引用 `dataset.field`。规范要求它命名一个 field；正文没有要求该 field 必须带 `dimension` 对象 | `external/ossie/core-spec/wide_table_semantics.md` 第 3.1、3.2 节 |
| 字段本身 | `DatasetField` 仍是数据集上的列：`id`、`name`、`data_type`、`description`、`user_visible`、`sensitive`。维度再从字段挑出来 | `backend/app/semantic/models.py` | `fields` 就是行级属性，用于分组、过滤和指标表达式。可选 `label`、`description`、`datatype`、`ai_context` | `external/ossie/core-spec/spec.md` 的 Fields |

本仓库因此有两份名单：数据集字段，以及从字段中挑出的维度。Ossie 的分组属性就是字段；`dimension` 只额外标记时间角色。`spec.yaml` 对 `dimension` 的注释写：Indicates this field can be used as a dimension for grouping/filtering。字段 schema 同时写所有 field 都可用于 grouping and filtering。这两句都在 `external/ossie/core-spec/spec.yaml` 的 Fields Schema，规范没有再定义“带了 `dimension` 才允许分组”。

### 数据集

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 必填身份 | `id`、`name`、`domain_id`、`dataset_type`、`grain`、`primary_key`、`fields`、`physical_mapping` | `backend/app/semantic/models.py` | `name`、`source`。`fields` 可选；没有字段的数据集对键、关系、模型级指标不暴露任何东西 | `external/ossie/core-spec/spec.md` 的 Datasets、Name Resolution |
| 种类 | `dataset_type`：`fact`、`dimension`、`aggregate`、`bridge`、`staging`、`source`。Jaffle Shop 用了 dimension、fact、staging、source | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | schema 没有 `dataset_type`。正文和术语表用 fact table、dimension table 描述 dataset 的典型形态 | `external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/docs/index.md` 术语表 Fact Table、Dimension Table |
| 一行代表什么 | `grain` 必填，例如 `order_mart` 的 `grain: [order_id]`。术语叫数据集粒度 | `backend/app/semantic/models.py`；`GLOSSARY.md`；`backend/configs/semantic/jaffle_shop.yaml` | 模型 schema 没有 `grain`。`primary_key` 是唯一标识行的字段列表，Layer 2 把它当作行身份的一个自然来源 | `external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/core-spec/relational_semantics.md` 第 3.4 节 |
| 键 | `primary_key` 必填，至少一项 | `backend/app/semantic/models.py` | `primary_key` 可选。另有 `unique_keys`：多组唯一键，每组是字段名列表 | `external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/core-spec/spec.yaml` 的 Logical Dataset Schema |
| 字段表达式 | 字段没有表达式。物理列名在 `column_mappings` | `backend/app/semantic/models.py` | 每个 field 必有 `expression`。标识符指向该数据集 `source` 的列，不能指向另一个 field | `external/ossie/core-spec/spec.md` 的 Fields、Name Resolution |
| 类型名 | `data_type` 是自由字符串。Jaffle Shop 使用 `integer`、`string`、`date`、`decimal` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | `datatype` 枚举：`String`、`Integer`、`Decimal`、`Float`、`Boolean`、`Date`、`Time`、`DateTime`、`DateTimeTz`、`Opaque`。省略表示未知 | `external/ossie/core-spec/spec.md` 的 Data types |
| 嵌套指标 | 指标不嵌在数据集里 | `backend/app/semantic/models.py` | 数据集可以带自己的 `metrics` | `external/ossie/core-spec/spec.md` 的 Datasets |

名字有三层，Ossie 写在 Name Resolution：源列、数据集字段、指标。字段表达式只见源列；键和关系只见字段；模型级指标只见 `dataset.field`。本仓库的字段 id 同时充当逻辑名，物理名只出现在 `column_mappings`。见 `external/ossie/core-spec/spec.md` 的 Name Resolution，以及 `backend/app/semantic/models.py` 的 `DatasetField`、`PhysicalMapping`。

### 物理映射

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 数据源 | `DataSource`：`id`、`name`、`dialect`、`description`。Jaffle Shop 是 `jaffle_duckdb`，`dialect: duckdb`。规范写明连接配置不属于模型，真实物理 Catalog 由外部只读接口传入 | `backend/app/semantic/models.py`；`docs/semantic-model/canonical-semantic-model-v1.md`；`backend/configs/semantic/jaffle_shop.yaml` | 模型没有数据源对象，也没有连接字段。方言写在每条表达式的 `dialects[].dialect` 上。`OSSIE_SQL_2026` 是规范定义的唯一方言，其他值不透明 | `external/ossie/core-spec/spec.md` 的 Dialects、Expression Object |
| 物理对象 | `PhysicalMapping` 挂在每个数据集上：`data_source_id`、`schema_name`（默认 `main`）、`object_name`、`object_type`（`table` 或 `view`）、`column_mappings`。例如 `order_mart` 映射到 `orders` 表 | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 必填字符串 `source`。说明文字给出的形式是 `database.schema.table`，或一条 query。示例有 `sales.public.orders` | `external/ossie/core-spec/spec.md` 的 Datasets；`external/ossie/core-spec/spec.yaml` 的 Logical Dataset Schema |
| 列对应 | `column_mappings` 是字段 id 到物理列名的字典 | `backend/app/semantic/models.py` | 不声明源列清单。字段 `expression.sql` 可以是列名，也可以是标量计算，例如 `first_name \|\| ' ' \|\| last_name`。重命名靠字段名和表达式分开 | `external/ossie/core-spec/spec.md` 的 Fields、Name Resolution |
| 谁消费物理位置 | 校验器收到外部 Catalog；模型内保留映射 | `docs/semantic-model/canonical-semantic-model-v1.md` | 规范写：直接对源表生成 SQL 的消费者，要把字段引用替换成字段表达式，把 `source.column` 换成列名 | `external/ossie/core-spec/spec.md` 的 Name Resolution |

本体映射不是物理映射。`ontology/ontology.md` 的 Ontology mappings 写：它把逻辑层字段的值映射到本体里的对象和链接，并且引用一份语义模型和一份本体，不把二者嵌进来。物理位置仍留在语义模型的 `dataset.source`。

### 关系

本仓库把关系拆成两种。Ossie 核心模型只有数据集之间的一种；概念之间的关系在本体文档里，查询接口又给关系加了发现元数据。

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 业务对象之间 | `BusinessRelationship`：`source_object_id`、`target_object_id`、`cardinality`、`verification_status`、`description`。基数枚举是 `one_to_one`、`one_to_many`、`many_to_many`。Jaffle Shop 的 Customer → Order、Order → Payment 都是 `one_to_many`、`proposed` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml`；`GLOSSARY.md` | 核心模型没有业务对象关系。本体关系挂在扮演第一角色的概念下，标识是 `Concept.name`。字段有 `name`、`multiplicity`、`roles`、`verbalizes`，可选 `derived_by`、`requires`、`iri`。基数枚举是 `ManyToOne`、`OneToOne` | `external/ossie/ontology/ontology.md` 的 Relationships、Multiplicities |
| 数据集之间 | `DatasetRelationship`：两端数据集、`source_fields`、`target_fields`、`cardinality`、`join_type`、`priority`、`fanout_risk`、`verification_status`，以及可选 `match_rate`、`orphan_count` | `backend/app/semantic/models.py` | `relationship`：`name`、`from`、`to`、`from_columns`、`to_columns`，可选 `ai_context`、`nonportable_extensions`。`from` 是 many 侧，`to` 是 one 侧。两端字段数组等长且顺序对应。列名是字段名，不是源列名 | `external/ossie/core-spec/spec.md` 的 Relationships |
| 基数在模型里是否显式 | 数据集关系的基数是 `one_to_one`、`many_to_one`、`one_to_many`、`many_to_many` | `backend/app/semantic/models.py` | 核心关系 schema 没有 cardinality 字段。`docs/index.md` 术语表写 relationships are always many-to-one。`spec.yaml` 注释写 primary key 与 unique keys 用于判断 many-to-one 或 one-to-one | `external/ossie/docs/index.md` 术语表 Relationship；`external/ossie/core-spec/spec.yaml` 的 Logical Dataset Schema、Relationship Schema |
| 连接方式 | `join_type` 为 `inner` 或 `left`，默认 `left`。`priority` 默认 100 | `backend/app/semantic/models.py` | 模型 schema 没有 join type 或 priority。Layer 3 降低时对路径使用 `LEFT JOIN`。Layer 2 写明 join type 由工具选择 | `external/ossie/core-spec/wide_table_semantics.md` 第 4 节；`external/ossie/core-spec/relational_semantics.md` 第 11.3 节 |
| 重复计数 | `fanout_risk`。术语 Fanout 指一侧一条记录关联后变成多条。规范要求有 Fanout 风险时先聚合回声明粒度。Jaffle Shop 里支付指向订单的两条关系为 `fanout_risk: true` | `GLOSSARY.md`；`docs/semantic-model/canonical-semantic-model-v1.md`；`backend/configs/semantic/jaffle_shop.yaml` | 关系上没有 fanout 字段。Layer 2 的保证是度量按自己的行身份去重，连接放大行不会让度量重复计算。Layer 3 这一版只走 many-to-one 步 | `external/ossie/core-spec/relational_semantics.md` 第 8.3 节与附录 A.5；`external/ossie/core-spec/wide_table_semantics.md` 第 4 节 |
| 证据与确认 | `verification_status`：`proposed`、`profiled`、`confirmed`。`profiled` 可带 `match_rate`、`orphan_count`。Jaffle Shop 五条数据集关系都是 `profiled` | `backend/app/semantic/models.py`；`GLOSSARY.md`；`backend/configs/semantic/jaffle_shop.yaml` | 关系 schema 没有验证状态、匹配率或孤儿数 | `external/ossie/core-spec/spec.md` 的 Relationships |
| 查询接口里的关系 | 本仓库模型关系就是上面两种 | `backend/app/semantic/models.py` | Layer 2 第 11.3 节描述可发现关系：名称、目标、等值连接谓词、显式基数（`ONE_TO_ONE`、`ONE_TO_MANY`、`MANY_TO_ONE`、`MANY_TO_MANY`）、可选目标别名。同一节写明显式基数、语义角色和任意布尔 `ON` 在当前 Ossie 关系元数据里没有位置，不能往返，除非以后扩展 schema | `external/ossie/core-spec/relational_semantics.md` 第 11.3 节 |
| 多条路径 | 数据集关系有 `priority`。规范写 `validate_dataset_join` 拒绝同优先级歧义 | `docs/semantic-model/canonical-semantic-model-v1.md`；`backend/app/semantic/models.py` | Layer 3：从 X 到 Y 有两条及以上路径时必须报 `E_AMBIGUOUS_PATH`，即使一条更短。关系图的边就是核心 `relationship` | `external/ossie/core-spec/wide_table_semantics.md` 第 4 节 |

本体关系的语义是集合，链接不含 null，并且用 `verbalizes` 给出读法。这是概念层结构。核心 `relationship` 的语义是数据集之间的外键式连接。两边文件分别是 `external/ossie/ontology/ontology.md` 的 Relationships，和 `external/ossie/core-spec/spec.md` 的 Relationships。本仓库的业务关系更接近前者的位置，数据集关系更接近后者；字段集合仍然不同。

### 主题空间

主题空间的类型是 `SpaceDefinition`，它不在 `SemanticModel` 里面。和“谁可以查、哪条关系可以用于计划”有关的结构还包括下面这些字段和术语。

| 结构 | 本仓库 | 本仓库文件 | Ossie | Ossie 文件 |
| --- | --- | --- | --- | --- |
| 主题空间这个词 | 术语表有销售主题空间、订单主题空间，定义可查询的指标、维度和数据。它们不是 `SemanticModel` 的字段。Jaffle Shop 只在 `orders.order_amount` 的 `definition` 正文里出现“订单主题空间” | `GLOSSARY.md`；`backend/configs/semantic/jaffle_shop.yaml` | 见本节末的检索记录 | 见下 |
| 模型内的分组 | `Domain` 把业务对象和数据集分到主题。Jaffle Shop 的域是 `sales` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 模型边界是文档的 `name`。Layer 2 第 11.2 节讨论如何把一个模型的关系发现出来，包括放到同一 schema 或可选的 `MODEL` 标记。那是发现边界，正文没有把它定义成授权 | `external/ossie/core-spec/spec.md`；`external/ossie/core-spec/relational_semantics.md` 第 11.2 节 |
| 数据集暴露 | `exposure_policy`：`internal`、`restricted`、`approved`，默认 `internal`。Jaffle Shop 里 `customer_mart` 为 `restricted`，`order_mart` 为 `approved` | `backend/app/semantic/models.py`；`backend/configs/semantic/jaffle_shop.yaml` | 数据集 schema 没有 exposure 或 policy 字段 | `external/ossie/core-spec/spec.md` 的 Datasets |
| 字段与属性可见性 | `DatasetField.user_visible`、`DatasetField.sensitive`；`BusinessAttribute.restricted`；`Dimension.user_visible` | `backend/app/semantic/models.py` | 字段可选 `label`，注释示例是 `"filter"`。没有可见性、敏感或受限布尔值 | `external/ossie/core-spec/spec.yaml` 的 Fields Schema |
| 指标是否发布 | `Metric.published`，默认 false | `backend/app/semantic/models.py` | 指标 schema 没有 published | `external/ossie/core-spec/spec.md` 的 Metrics |
| 关系能否进入查询计划 | 规范写：`profiled` 只记录只读探查证据；查询计划还需要点名一条获授权的 `confirmed` 数据集关系。`validate_dataset_join` 拒绝未声明、未授权、同优先级歧义及未处理 Fanout 的关联 | `docs/semantic-model/canonical-semantic-model-v1.md`；状态枚举在 `backend/app/semantic/models.py` 和 `GLOSSARY.md` | 关系 schema 没有验证状态，也没有“已授权”字段 | `external/ossie/core-spec/spec.md` 的 Relationships |
| 决策里的“授权” | ADR 写模型使确定性查询和未来灵活 SQL 路径共享同一套口径与授权。正文没有再定义授权空间的类型 | `docs/adr/0002-canonical-semantic-model.md` | 见检索记录 | 见下 |

Ossie 里没有找到授权空间，也没有找到与 `exposure_policy`、`published`、`confirmed` 对应的模型字段。检索过这些文件：

- `external/ossie/README.md`
- `external/ossie/docs/index.md`（含术语表）
- `external/ossie/core-spec/spec_layers.md`
- `external/ossie/core-spec/spec.md`
- `external/ossie/core-spec/spec.yaml`
- `external/ossie/core-spec/expression_language.md`
- `external/ossie/core-spec/relational_semantics.md`
- `external/ossie/core-spec/wide_table_semantics.md`
- `external/ossie/ontology/ontology.md`
- `external/ossie/ROADMAP.md`

检索词包括 access control、authorization、security、exposure、published，以及作为模型构造的 space。`space` 的命中是空白、命名空间或填充，不是授权空间。

两处相邻文字确实存在，它们都没有给出模型字段：

- `external/ossie/ROADMAP.md` 的 Catalog Integration 交付项写有 Discovery, versioning, and access control for Ossie models。
- `external/ossie/core-spec/relational_semantics.md` 第 13.2 节把 Row-level and column-level security 与度量求值的交互列为本版不处理的相邻问题。

## 不对齐的地方

以下各项两边都有明确正文，结构对不上。

1. 业务对象与数据集的拆分不同。本仓库的 `BusinessObject` 通过 `dataset_ids` 挂到多个数据集，属性是另一份列表。Ossie Layer 1 用 dataset 表示 business entity；Customer、Order 这一层的类型、继承、标识关系和口头读法写在 `ontology/ontology.md`，并且是另一份文档。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`，`external/ossie/ontology/ontology.md`。

2. 维度是不是一等对象不同。本仓库的 `Dimension` 另有 id、显示名、同义词、兼容指标和 `user_visible`，并指向数据集字段。Ossie 的维度是字段上的 `dimension.is_time`。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`，`external/ossie/docs/index.md`。

3. `is_time` 的默认不同。本仓库布尔默认 false，所以日期字段要显式打开。Ossie 对 `Date`、`Time`、`DateTime`、`DateTimeTz` 默认 true。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`。

4. 指标表达式的载体不同。本仓库是封闭的 `kind` 节点，比例和指标引用都是结构字段。Ossie 是 SQL 文本加可选方言，比例写在表达式里，跨指标只允许 `AGG(dataset.metric)`。本仓库每条指标绑定一个 `source_dataset_id`；Ossie 另有可跨数据集的模型级指标。文件：`backend/app/semantic/models.py`，`docs/semantic-model/canonical-semantic-model-v1.md`，`external/ossie/core-spec/spec.md`。

5. 指标上的分析约束不同。本仓库有 `base_grain`、`time_dimension_id`、`default_filters`、`compatible_dimension_ids`、`additive`、`unit`、`published`。Ossie 指标 schema 没有这些字段。Ossie 用数据集行级和 `primary_key` 行身份承担粒度角色，用 Layer 3 的可达性承担“能和哪些维度一起查”的角色。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`，`external/ossie/core-spec/relational_semantics.md`，`external/ossie/core-spec/wide_table_semantics.md`。

6. 物理映射的形状不同。本仓库是 `DataSource` 加结构化 `PhysicalMapping`，列对应是字典，对象类型只接受 `table` 和 `view`。Ossie 是一个 `source` 字符串，允许表、视图或 query；列对应是字段表达式；源列不在文档中列出；方言在表达式上，不在数据源上。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`。

7. 数据集的分类和粒度字段不同。本仓库有 `dataset_type` 和必填 `grain`。Ossie 用散文区分事实表和维度表，schema 另有 `unique_keys`，没有 `dataset_type` 和 `grain`。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`，`external/ossie/core-spec/spec.yaml`。

8. 关系的种类和基数写法不同。本仓库分开写业务关系与数据集关系，基数是显式枚举，数据集关系还可以写 `one_to_many` 和 `many_to_many`，并带 `join_type`、`priority`、`fanout_risk` 和验证证据。Ossie 核心关系把 `from` 放在 many 侧、`to` 放在 one 侧，没有这些字段。Layer 2 想要的显式基数写明目前不能写回核心关系。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`，`external/ossie/core-spec/relational_semantics.md`。

9. 授权相关字段只出现在本仓库模型里。`exposure_policy`、`published`、`user_visible`、`sensitive`、`restricted`，以及必须点名 `confirmed` 关系，都在本仓库。Ossie 当前模型 schema 没有对应字段；访问控制出现在路线图，行级和列级安全出现在 Layer 2 的未处理列表。文件：`backend/app/semantic/models.py`，`docs/semantic-model/canonical-semantic-model-v1.md`，`external/ossie/ROADMAP.md`，`external/ossie/core-spec/relational_semantics.md`。

10. 标识和说明的位置不同。本仓库把 `id` 和 `name` 分开，同义词只在 `Metric` 和 `Dimension` 上。Ossie 用 `name` 做标识，同义词、说明和示例放在模型、数据集、字段、关系、指标各级的 `ai_context`。Ossie 还有 `nonportable_extensions`；`models.py` 没有对应槽位。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md`。

11. 类型词汇不同。本仓库 `data_type` 是自由字符串。Ossie `datatype` 是固定枚举，并与本体的内建值类型对齐；`Time`、`DateTimeTz`、`Opaque` 是核心规范多出来的。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md` 的 Data types。

12. 一个模型里能不能有多个主题不同。本仓库一个 `SemanticModel` 包含 `domains` 列表。Ossie 一份文档一个模型，规范写明不定义 bundle，也不定义跨模型引用。文件：`backend/app/semantic/models.py`，`external/ossie/core-spec/spec.md` 的 Semantic Model。

## 未能确认的地方

1. `expression_language.md` 的 Not Supported in Expressions 写：`GROUP BY` 的理由是 Controlled by grain specification，`WHERE` 的理由是 Use filter property instead。在 `external/ossie/core-spec/spec.md` 和 `external/ossie/core-spec/spec.yaml` 的数据集、字段、指标 schema 里，没有名为 `grain` 或 `filter` 的模型字段。Layer 3 的 `filter` 属于查询，不属于模型。因此不能确认这两句指向哪一个已经存在的 schema 属性。`ROADMAP.md` 把 first-class grain semantics 和 reusable semantic filter definitions 列为交付项，并写当前模型对多粒度指标和过滤器支持不足。

2. 关系基数有三处说法，规则正文没有并到一处。`external/ossie/core-spec/spec.md` 的 Relationships 只写 `from` 是 many 侧、`to` 是 one 侧。`external/ossie/docs/index.md` 术语表写 relationships are always many-to-one。`external/ossie/core-spec/spec.yaml` 注释写 `primary_key` 与 `unique_keys` 用于判断 many-to-one 或 one-to-one。`spec.md` 里没有找到根据键推断 one-to-one 的算法。Layer 2 又说显式基数不在当前关系元数据中。不能确认一份核心模型文档如何无歧义地表达 one-to-one。

3. 本体表达式的名字解析没有在核心表达式规范里写完。`external/ossie/core-spec/expression_language.md` 写列和指标引用会因 Ontology 或 Semantic model 而不同，并把本体部分指到后续文档。`external/ossie/ontology/ontology.md` 说明了 `derived_by` 和 `requires` 必须引用哪些概念或角色，但没有给出与 `spec.md` Name Resolution 同级的标识符表。不能确认本体表达式与语义模型字段表达式共用同一套解析规则。

4. 路线图与本体规范的完成状态。`external/ossie/ROADMAP.md` 仍把显式实体建模、跨域建模和本体层列为交付项。`external/ossie/ontology/ontology.md` 与 `external/ossie/core-spec/spec_layers.md` 已经把 Layer 4 定义为当前规范，版本 `0.3.0.dev0`。不能从这两处判断哪些本体字段被视为已稳定。

5. 本仓库的主题空间、暴露和发布标志、以及 `confirmed` 关系，各自覆盖不同范围，不能合成一个 Ossie 访问控制对象。`GLOSSARY.md` 定义主题空间，`SpaceDefinition` 承载它；`models.py` 有暴露和发布标志；`canonical-semantic-model-v1.md` 要求点名 `confirmed` 关系。Ossie 侧只确认了路线图中的 access control 一句，以及 Layer 2 对行级、列级安全的排除。不能确认 Ossie 以后的访问控制会落在模型字段、目录服务，还是查询接口。
