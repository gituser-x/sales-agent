# 接口设计（脱敏摘要）

下面路径及模型来自原后端路由。接口是否对演示账号开放，以服务端权限为准；授权头、Cookie 和真实会话记录不公开。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/agent/run | 执行一次 Agent 请求 |
| POST | /api/agent/stream | SSE 流式执行 |
| GET | /api/agent/tools | 查询当前用户可用工具 |
| POST | /api/agent/tools/call | 受控工具调用 |
| POST | /api/agent/tasks/{task_id}/confirm | 确认或拒绝运营任务 |
| GET | /api/chat/sessions | 当前用户的会话列表 |
| GET | /api/chat/sessions/{session_id} | 当前用户的会话详情 |
| POST | /api/chat/attachments | 临时附件上传 |

## Agent 请求

```json
{"message":"家长认为课程价格较高，如何回复？","knowledge_scope":"sales","tool_mode":"auto"}
```

`message` 长度 1–2000 且不可全空白；`knowledge_scope` 为 all / subject / course / policy / family / sales；`tool_mode` 为 auto / sales_assist / kb_ops。`session_id`、`customer_id`、`attachment_ids`、`confirmation` 为可选上下文。

## 关键响应字段

`content` 为回答；`citations` 为知识引用；`retrieval_summary` 展示命中数量与来源；`pending_confirmation` 为待确认动作；`recommended_courses` 为课程推荐结果；`route` / `tool_name` 标识执行路径。字段是否填充取决于实际分支。

合成样例：[请求](../examples/requests/agent-run.json)、[响应](../examples/responses/agent-run.json)。样例不是生产日志，不代表真实命中或效果。
