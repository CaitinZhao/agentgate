# FinanceBench PDF 语料（不入 git，运行时挂载）

本目录存放 FinanceBench 真实财报 PDF（约 158MB，84 份 10-K/10-Q），供 fb 剖面的
`search_filing` / `list_filings` 工具检索。语料属第三方公开数据集，**不提交到仓库**。

获取方式（二选一）：

1. 从 FinanceBench 官方仓库下载 PDF 目录：
   https://github.com/ParetoIntel/financebench（data/pdfs/）
2. 若你手头有旧部署：服务器上没有；PDF 只随 deploy_eval 的 bundle 传递。
   可在任一有完整工作区的机器执行打包流程后从 bundle 提取，或直接按 1 下载。

放置要求：本目录下平铺 `<doc_name>.pdf`（doc_name 与 FinanceBench 的
`financebench_document_information.jsonl` 一致，如 `AMERICANWATERWORKS_2022_10K.pdf`）。

容器接线：`docker-compose.yml` 已把它只读挂载到 Agent 容器的 `/app/fb_data`；
不挂载时 fb 剖面题目的检索结果为空（对应题目会按金标如实判 FAIL）。
