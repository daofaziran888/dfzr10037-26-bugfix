"""复现脚本：子图探查结果数量缺失/显式零/重试场景。"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from app.store import store  # noqa: E402
from app.services.subgraph_exp import SubgraphExpService  # noqa: E402

svc = SubgraphExpService()

# 场景 1：登记没有“结果数量”的探查配置（前端登记时该字段留空）
entry, missing = svc.create_entry({
    "配置编号": "SUBG-T-0001",
    "探查种子": "entity-empty",
    "扩展策略": "bfs",
    "关系过滤": "",
    "深度限制": "2",
    # 不传“结果数量”
})
print("场景1 登记:", entry is not None, "missing=", missing, "结果数量=", entry and entry.get("结果数量"))

# 场景 2：显式 0
entry0, _ = svc.create_entry({
    "配置编号": "SUBG-T-0002",
    "探查种子": "entity-zero",
    "扩展策略": "bfs",
    "关系过滤": "",
    "深度限制": "2",
    "结果数量": "0",
})
print("场景2 登记显式0:", entry0 and entry0.get("结果数量"), type(entry0 and entry0.get("结果数量")))

# 启动探查（会推送 graph_search 和 reco_engine）
eid = entry["id"]
res, msg = svc.run_action(eid, "启动探查")
print("场景1 启动探查:", res is not None, msg)

# 重试：再启动一次（模拟探查中断后重试）
res2, msg2 = svc.run_action(eid, "启动探查")
print("场景1 重试启动:", msg2)

# 检查 graph_search 与 reco_engine 里的衍生记录
gs = [r for r in store.rows("graph_search") if r.get("起始实体") == "entity-empty"]
reco = [r for r in store.rows("reco_engine") if r.get("源实体") == "entity-empty"]
print("graph_search 衍生份数:", len(gs), [(r["模板编号"], r.get("abnormal"), r.get("pending"), r.get("来源配置")) for r in gs])
print("reco_engine 衍生份数:", len(reco), [(r["规则编号"], r.get("abnormal"), r.get("候选数量")) for r in reco])

# 显式 0 的启动
res0, msg0 = svc.run_action(entry0["id"], "启动探查")
print("场景2 显式0启动:", res0 is not None, msg0)
gs0 = [r for r in store.rows("graph_search") if r.get("起始实体") == "entity-zero"]
reco0 = [r for r in store.rows("reco_engine") if r.get("源实体") == "entity-zero"]
print("显式0 graph_search:", [(r["模板编号"], r.get("邻边数量"), r.get("探查结论")) for r in gs0])
print("显式0 reco:", [(r["规则编号"], r.get("候选数量")) for r in reco0])
