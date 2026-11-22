"""端到端核验：子图探查缺值/显式零/重试/非零的完整交接链路。

覆盖需求：
1. 从子图探查工作台登记没有数量的数据（缺值不报错，且不等于零邻居）；
2. 探查中断后重试只确认一份同源模板和推荐材料，历史重复草稿标记异常仅供追查；
3. 重试请求后填入明确的零 → 真正空结果；
4. 再测普通非零输入 → 结果同步推荐待办作为候选取数依据；
5. 检查残留草稿、实际可运行模板与推荐候选数据。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from app.store import store  # noqa: E402
from app.services.subgraph_exp import SubgraphExpService  # noqa: E402
from app.services.graph_search import GraphSearchService  # noqa: E402
from app.services.reco_engine import RecoEngineService  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label} {detail}")
    if not condition:
        failures.append(label)


sub_svc = SubgraphExpService()
gs_svc = GraphSearchService()
reco_svc = RecoEngineService()

# ---------- 解析单测：缺值 vs 显式 0 vs 非零 ----------
p = SubgraphExpService.parse_result_count
check("解析-空串→None(数量未得到)", p("") is None)
check("解析-None→None", p(None) is None)
check("解析-空白→None", p("   ") is None)
check("解析-乱码→None(不报错)", p("abc") is None)
check("解析-显式字符串0→整数0", p("0") == 0 and isinstance(p("0"), int))
check("解析-显式数字0→0", p(0) == 0)
check("解析-非零字符串→整数", p("12") == 12)
check("解析-小数字符串→整数", p("7.0") == 7)

# ---------- 场景 A：登记没有数量的数据 ----------
entry_a, missing = sub_svc.create_entry({
    "配置编号": "SUBG-E2E-A",
    "探查种子": "seed-missing",
    "扩展策略": "bfs",
    "关系过滤": "rel-x",
    "深度限制": "2",
    "结果数量": "",
})
check("A-登记无数量成功", entry_a is not None and missing == [], str(missing))
check("A-缺值规范化为None", entry_a.get("结果数量") is None, repr(entry_a.get("结果数量")))

# 缺值状态下缓存必须被拒绝（缺值不得当零邻居交付）
blocked, block_msg = sub_svc.run_action(entry_a["id"], "缓存结果")
check("A-缺值时拒绝缓存", blocked is None and "尚未取得" in block_msg, block_msg)

# 启动探查不再抛 TypeError
res_a, msg_a = sub_svc.run_action(entry_a["id"], "启动探查")
check("A-缺值启动探查不报错", res_a is not None, msg_a)
check("A-消息声明缺值非零邻居", "缺值" in msg_a and "零邻居" in msg_a, msg_a)

gs_a = [r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-A"]
reco_a = [r for r in store.rows("reco_engine") if r.get("来源配置") == "SUBG-E2E-A"]
check("A-生成且仅生成1份搜索草稿", len(gs_a) == 1, str([r["模板编号"] for r in gs_a]))
check("A-生成且仅生成1份推荐草稿", len(reco_a) == 1, str([r["规则编号"] for r in reco_a]))
check("A-草稿可由操作人员打开(草稿态)", gs_a[0]["status"] == "草稿" and gs_a[0]["abnormal"] is False)
check("A-推荐候选数量留空而非0", reco_a[0]["候选数量"] == "", repr(reco_a[0]["候选数量"]))
check("A-模板结论标注数量待取得", "待取得" in gs_a[0]["探查结论"], gs_a[0]["探查结论"])
check("A-交接状态可续做", "续做" in gs_a[0]["交接状态"], gs_a[0]["交接状态"])

# ---------- 场景 B：探查中断后重试（先制造历史重复，再重试归并） ----------
# 模拟旧版本残留：手工再塞一份同源草稿，重试时应被标记异常而非继续扩散。
gs_rows = store.rows("graph_search")
old_gs = dict(gs_a[0])
old_gs_id = max(int(r["id"]) for r in gs_rows) + 1
old_gs.update({"id": old_gs_id, "模板编号": f"GRAP-SUB-{old_gs_id:04d}", "abnormal": False})
gs_rows.append(old_gs)
reco_rows = store.rows("reco_engine")
old_reco = dict(reco_a[0])
old_reco_id = max(int(r["id"]) for r in reco_rows) + 1
old_reco.update({"id": old_reco_id, "规则编号": f"RECO-SUB-{old_reco_id:04d}", "abnormal": False})
reco_rows.append(old_reco)

res_a2, msg_a2 = sub_svc.run_action(entry_a["id"], "启动探查")  # 中断后重试
check("B-重试不报错", res_a2 is not None, msg_a2)
gs_a2 = [r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-A"]
reco_a2 = [r for r in store.rows("reco_engine") if r.get("来源配置") == "SUBG-E2E-A"]
check("B-重试不新增模板(仍2份=1有效+1异常)", len(gs_a2) == 2)
check("B-重试不新增推荐(仍2份=1有效+1异常)", len(reco_a2) == 2)
valid_gs = [r for r in gs_a2 if not r["abnormal"]]
valid_reco = [r for r in reco_a2 if not r["abnormal"]]
abn_gs = [r for r in gs_a2 if r["abnormal"]]
abn_reco = [r for r in reco_a2 if r["abnormal"]]
check("B-恰好1份有效模板", len(valid_gs) == 1 and "重复草稿" in abn_gs[0]["交接状态"])
check("B-恰好1份有效推荐", len(valid_reco) == 1 and abn_reco[0]["abnormal"] is True)
check("B-消息提示异常草稿仅供追查", "仅供追查" in msg_a2, msg_a2)
check("B-两侧携带同一来源配置可互相核查",
      valid_gs[0]["来源配置"] == valid_reco[0]["来源配置"] == "SUBG-E2E-A"
      and valid_reco[0]["来源模板编号"] == valid_gs[0]["模板编号"])
check("B-探查登记交接状态=数量待取得可续做", entry_a["交接状态"] == "数量待取得·草稿可续做", entry_a["交接状态"])

# 图谱搜索侧读取：默认隐藏异常草稿，追查时可显式打开
drafts = gs_svc.subgraph_drafts()
check("B-图谱搜索默认只列有效草稿", all(not d["abnormal"] for d in drafts)
      and any(d["来源配置"] == "SUBG-E2E-A" for d in drafts))
drafts_all = gs_svc.subgraph_drafts(include_abnormal=True)
check("B-追查模式可见异常草稿", any(d["abnormal"] and d["来源配置"] == "SUBG-E2E-A" for d in drafts_all))
seed_mark = next(d for d in drafts if d["来源配置"] == "SUBG-E2E-A")
check("B-模板列表标明可采用种子与过滤", seed_mark["可采用种子"] == "seed-missing"
      and seed_mark["可采用过滤"] == "rel-x", str(seed_mark))

# ---------- 场景 C：重试请求后填入明确的零（真正空结果） ----------
entry_a["结果数量"] = SubgraphExpService.parse_result_count("0")
check("C-填入显式0为整数0", entry_a["结果数量"] == 0 and isinstance(entry_a["结果数量"], int))
res_c, msg_c = sub_svc.run_action(entry_a["id"], "启动探查")
check("C-显式0重试不报错", res_c is not None, msg_c)
check("C-消息确认真正空子图", "空子图" in msg_c and "显式 0" in msg_c, msg_c)
gs_c = [r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-A" and not r["abnormal"]]
reco_c = [r for r in store.rows("reco_engine") if r.get("来源配置") == "SUBG-E2E-A" and not r["abnormal"]]
check("C-仍只有1份有效模板(复用)", len(gs_c) == 1)
check("C-仍只有1份有效推荐(复用)", len(reco_c) == 1)
check("C-模板标记已确认空结果", gs_c[0]["探查结论"].startswith("已确认空结果"), gs_c[0]["探查结论"])
check("C-推荐候选数量为0且非空", reco_c[0]["候选数量"] == 0, repr(reco_c[0]["候选数量"]))
check("C-推荐数据状态=无推荐候选", "无推荐候选" in reco_c[0]["数据状态"], reco_c[0]["数据状态"])
empty_subs = gs_svc.confirmed_empty_subgraphs()
check("C-空子图清单可读到该模板", any(d["来源配置"] == "SUBG-E2E-A" for d in empty_subs))
# 显式 0 后允许缓存成交付
cached, cache_msg = sub_svc.run_action(entry_a["id"], "缓存结果")
check("C-显式0允许缓存交付", cached is not None and cached["status"] == "已缓存", cache_msg)
check("C-缓存不新增消费记录",
      len([r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-A"]) == 2)

# ---------- 场景 D：普通非零输入 ----------
entry_d, _ = sub_svc.create_entry({
    "配置编号": "SUBG-E2E-D",
    "探查种子": "seed-normal",
    "扩展策略": "dfs",
    "关系过滤": "rel-y",
    "深度限制": "3",
    "结果数量": "9",
})
res_d, msg_d = sub_svc.run_action(entry_d["id"], "启动探查")
check("D-非零探查成功", res_d is not None, msg_d)
reco_d = [r for r in store.rows("reco_engine") if r.get("来源配置") == "SUBG-E2E-D" and not r["abnormal"]]
gs_d = [r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-D" and not r["abnormal"]]
check("D-推荐候选数量同步为9", len(reco_d) == 1 and reco_d[0]["候选数量"] == 9, str(reco_d))
check("D-推荐数据状态标明取数依据", "取数依据" in reco_d[0]["数据状态"], reco_d[0]["数据状态"])
check("D-模板结论带9条邻边", "9 条邻边" in gs_d[0]["探查结论"], gs_d[0]["探查结论"])
check("D-探查交接状态已确认", entry_d["交接状态"] == "结果已确认·两侧已交接", entry_d["交接状态"])
cand = reco_svc.subgraph_candidates()
check("D-推荐侧可读取候选取数依据",
      any(c["来源配置"] == "SUBG-E2E-D" and c["候选数量"] == 9 for c in cand))

# ---------- 场景 E：缺值新登记的探查（第二个缺值源）不与 A 串单 ----------
entry_e, _ = sub_svc.create_entry({
    "配置编号": "SUBG-E2E-E",
    "探查种子": "seed-other",
    "扩展策略": "bfs",
    "关系过滤": "",
    "深度限制": "1",
})
sub_svc.run_action(entry_e["id"], "启动探查")
gs_e = [r for r in store.rows("graph_search") if r.get("来源配置") == "SUBG-E2E-E"]
check("E-不同来源各自1份模板", len(gs_e) == 1 and gs_e[0]["起始实体"] == "seed-other")
check("E-缺值推荐候选仍留空",
      [r for r in store.rows("reco_engine") if r.get("来源配置") == "SUBG-E2E-E"][0]["候选数量"] == "")

# ---------- 汇总：残留草稿 / 可运行模板 / 推荐候选 ----------
snap_sub = sub_svc.domain_snapshot()
snap_gs = gs_svc.domain_snapshot()
snap_reco = reco_svc.domain_snapshot()
print("\n=== 快照 ===")
print("subgraph:", snap_sub)
print("graph_search:", snap_gs)
print("reco_engine:", snap_reco)
check("快照-缺值计数=1(E)", snap_sub["missing_result_count"] == 1, str(snap_sub))
check("快照-空结果计数=1(A)", snap_sub["confirmed_empty_subgraph"] == 1, str(snap_sub))
check("快照-图谱搜索草稿数不含异常", snap_gs["subgraph_drafts"] == 3, str(snap_gs))
check("快照-已确认空子图=1", snap_gs["confirmed_empty_subgraphs"] == 1, str(snap_gs))
check("快照-推荐候选数不含异常", snap_reco["subgraph_candidates"] == 3, str(snap_reco))

print("\n结果：", "全部通过 ✅" if not failures else f"{len(failures)} 项失败 ❌ -> {failures}")
sys.exit(1 if failures else 0)
