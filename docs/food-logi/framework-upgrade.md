# 框架升级

`products` 的直接框架上游是 FastapiAdmin，建议 remote 名为 `framework`。升级时先获取并在专用分支合并 `framework/master`，再运行三 Assembly 契约、认证回归、三空库迁移和三个前端构建。

产品模块、Seed、品牌配置和部署数据不回流通用框架。框架通用修复需单独整理后再提交给上游。
