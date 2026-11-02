# 核心代码导览

此目录保存实际项目的白名单代码节选，保留业务依赖关系用于审阅，没有填入虚假的数据库或模型实现。除文本评分模块外，不能作为独立应用启动。

| 文件 | 内容 |
| --- | --- |
| [agent/graph.py](agent/graph.py) | 原状态图定义与节点计时 |
| [agent/tool_registry.py](agent/tool_registry.py) | 工具规格、注册与发现 |
| [agent/tool_context.py](agent/tool_context.py) | 用户、权限与 trace 上下文 |
| [agent/tool_executor.py](agent/tool_executor.py) | 校验、权限、重试、软超时与审计 |
| [retrieval/hybrid_search.py](retrieval/hybrid_search.py) | 混合检索主流程与降级 |
| [retrieval/text_scoring.py](retrieval/text_scoring.py) | 可独立运行的轻量评分与 n-gram |
| [workers/ingest_worker.py](workers/ingest_worker.py) | 消费、重投、重试与死信处理 |
| [workers/rabbitmq_service.py](workers/rabbitmq_service.py) | 队列拓扑与消息发布 |
| [uploads/stream_assembly.py](uploads/stream_assembly.py) | 分片流式合并与完整性校验 |

来源与校验值见 [代码来源清单](../docs/source-manifest.json)。节选中 `app.*` 等私有依赖有意省略；语法检查可通过，但不宣称这些模块可在本仓库直接导入运行。
