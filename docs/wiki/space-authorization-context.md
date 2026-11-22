# 空间授权与语义上下文

## 决定

2026-11-21，用户要求在不改变空间授权边界的前提下，为语义资产增长准备更小的模型上下文。同日在 `5600d5c` 上的审计确认：业务对象、指标、维度、数据集、业务关系和数据集 JOIN 已有稳定 ID；订单 Direct SQL 会把整份授权写进提示，但提示和校验不是同一次构建。

同日决定，见 [ADR 0006](../adr/0006-space-authorization-bounds-retrieval.md)：

- 空间授权是硬边界。
- 检索只决定本次提示展示哪些已授权资产。
- 校验器使用完整空间授权。
- 规范语义模型是唯一业务口径。Wiki 不作为运行时口径。

订单空间继续走全量 Direct SQL。短目录、语义展开和上下文覆盖缺失留给超过小空间规模的授权。支付关系、raw 和 staging 不因检索补全而进入授权。

## 相关页

- [订单主题 Direct SQL](direct-sql-orders.md)
- [客户–订单授权上下文](customer-order-context.md)
- [支付明细与 Fanout](payment-fanout.md)
