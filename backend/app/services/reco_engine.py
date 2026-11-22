"""推荐引擎领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.store import store


class RecoEngineService(BaseModuleService):
    MODULE = "reco_engine"
    ENTITY = "推荐规则"
    OPERATION_TERM = "关联"
    LIST_FIELDS = ["规则编号", "推荐场景", "源实体", "推荐算法", "候选数量", "过滤条件", "命中次数", "采纳率", "来源配置", "来源模板编号", "数据状态"]
    REQUIRED_FIELDS = ["规则编号", "推荐场景", "源实体"]
    STATUS_ORDER = ["草稿", "已生效", "效果差", "已停用"]
    ACTION_RULES = {"启效规则": "已生效", "标记差效": "效果差", "停用规则": "已停用"}
    NEGATIVE_ACTIONS = ["停用规则"]

    # ===== 读取子图探查确认结果同步过来的推荐候选取数依据 =====
    def subgraph_candidates(self, *, include_abnormal: bool = False) -> list[dict]:
        """子图探查来源的推荐待办：候选数量以已确认结果为取数依据。

        缺值阶段“候选数量”为空（待探查确认，不得按 0 候选处理）；
        显式 0 的空子图同步为无推荐候选；异常重复草稿默认隐藏仅供追查。
        """
        items: list[dict] = []
        for row in store.rows(self.MODULE):
            if row.get("来源模块") != "subgraph_exp":
                continue
            if row.get("abnormal") and not include_abnormal:
                continue
            items.append({
                "规则编号": row.get("规则编号", ""),
                "源实体": row.get("源实体", ""),
                "候选数量": row.get("候选数量", ""),
                "过滤条件": row.get("过滤条件", ""),
                "来源配置": row.get("来源配置", ""),
                "来源模板编号": row.get("来源模板编号", ""),
                "数据状态": row.get("数据状态", ""),
                "abnormal": bool(row.get("abnormal")),
            })
        return items

    def domain_snapshot(self) -> dict:
        base = super().domain_snapshot()
        base["subgraph_candidates"] = len(self.subgraph_candidates())
        base["subgraph_candidates_missing_count"] = sum(
            1 for row in self.subgraph_candidates() if row.get("候选数量") in ("", None)
        )
        return base
