# RAG 检索链路

1. 查询分析：按配置进行查询改写，识别问题类别及实体锚点。
2. 混合召回：Milvus 中进行稠密向量与 BM25 检索，使用 RRF 融合排序。
3. 数据校验：结合文档有效性、元数据范围、知识类型及实体相关性过滤候选。
4. 重排序：结合配置、业务规则与 reranker 对候选重新排序并去重。
5. 降级：向量异常或无结果时尝试 MySQL 关键词查询；更宽泛的回退有适用条件，不等于任意资料均可作为事实依据。
6. 回答生成：将知识片段与会话上下文交给生成链路，返回引用与检索来源摘要。

主流程见 [hybrid_search.py](../code-excerpts/retrieval/hybrid_search.py)。稠密/BM25 索引与 RRF 位于私有 Milvus 客户端，因此该节选不能独立运行。

## 为什么组合多种检索方式

语义召回有助于覆盖不同表达；BM25 有助于匹配课程名称等词面信息；元数据和实体约束用于降低“语义相似但业务对象错误”的命中。具体效果需要以固定数据集验证，本仓库不填入未经测量的提升数字。

## 质量评估

完整工程提供 Ragas 与 LLM-as-Judge 入口，关注 faithfulness、answer relevancy、context precision、context recall。评估入口存在不代表已经取得某个成绩；公开版没有随附可复核的线上评测报告。

轻量文本评分与中文 n-gram 在 [text_scoring.py](../code-excerpts/retrieval/text_scoring.py)，其独立测试仅验证局部逻辑，不替代 RAG 端到端质量评估。
