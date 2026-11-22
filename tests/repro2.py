"""复现脚本2：部分写入残留 + 重试重复模板。"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from app.store import store  # noqa: E402
from app.services.subgraph_exp import SubgraphExpService  # noqa: E402

svc = SubgraphExpService()

# 正常登记（带数量），连续两次启动探查，观察重复衍生
entry, _ = svc.create_entry({
    "配置编号": "SUBG-T-0100",
    "探查种子": "entity-retry",
    "扩展策略": "bfs",
    "关系过滤": "rel-a",
    "深度限制": "2",
    "结果数量": "7",
})
svc.run_action(entry["id"], "启动探查")
svc.run_action(entry["id"], "启动探查")  # 中断后重试

gs = [r for r in store.rows("graph_search") if r.get("起始实体") == "entity-retry"]
reco = [r for r in store.rows("reco_engine") if r.get("源实体") == "entity-retry"]
print("重试后 graph_search 份数:", len(gs), [r["模板编号"] for r in gs])
print("重试后 reco_engine 份数:", len(reco), [r["规则编号"] for r in reco])

# 检查上一次失败场景留下的残留草稿（部分写入）
leftover = [r for r in store.rows("graph_search") if r.get("起始实体") == "entity-empty"]
print("失败场景残留 graph_search 草稿:", [(r["模板编号"], r.get("status"), r.get("abnormal")) for r in leftover])
