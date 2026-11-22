"""子图探查领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。

跨模块交接（subgraph_exp → graph_search / reco_engine）要点：
- “结果数量”缺值（登记时留空、重试尚未取得）规范化为 None，语义是“数量仍未得到”，
  绝不能当作已确认的零邻居；显式 0 才代表真正的空结果。
- 探查重试按同源“配置编号”归并，只确认一份搜索模板与一份推荐规则；
  历史重复草稿标记为 abnormal，仅供追查，不作为有效探查交付。
- 模板与推荐规则都可核查（携带同一来源配置编号、互相可追溯）才算完成交接。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 跨模块草稿的来源标记与共享溯源键，两个消费方据此核查同一份探查交接。
SOURCE_MODULE = "subgraph_exp"


class SubgraphExpService(BaseModuleService):
    MODULE = "subgraph_exp"
    ENTITY = "探查配置"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["配置编号", "探查种子", "扩展策略", "关系过滤", "深度限制", "结果数量", "缓存策略", "上次探查", "交接状态"]
    REQUIRED_FIELDS = ["配置编号", "探查种子", "扩展策略"]
    STATUS_ORDER = ["草稿", "探查中", "已缓存", "已丢弃"]
    ACTION_RULES = {"启动探查": "探查中", "缓存结果": "已缓存", "丢弃缓存": "已丢弃"}
    NEGATIVE_ACTIONS = []

    # ===== 结果数量解析：缺值与显式零必须区分 =====
    @staticmethod
    def parse_result_count(raw: Any) -> int | None:
        """把登记/重试入参规范化为 int 或 None。

        - None、空串、纯空白、无法解析的非数字 → None（数量仍未得到，不等于零邻居）
        - 数字字符串或数值 → int，其中显式 "0"/0 保留为 0（已确认空结果）
        """
        if raw is None:
            return None
        if isinstance(raw, bool):
            return int(raw)
        if isinstance(raw, int):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        try:
            return int(text)
        except (TypeError, ValueError):
            try:
                return int(float(text))
            except (TypeError, ValueError):
                return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        normalized = dict(values)
        # 登记阶段就固定数量语义：缺值为 None，显式 0 原样保留为整数 0。
        normalized["结果数量"] = self.parse_result_count(values.get("结果数量"))
        entry, missing = super().create_entry(normalized)
        if entry is not None:
            entry["交接状态"] = "未交接"
        return entry, missing

    # ===== 跨模块数据流实现 =====
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if action not in self.ACTION_RULES:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
        # 缺值不得当作已确认零邻居：未取得数量前不允许缓存成交付结果。
        if action == "缓存结果" and entry.get("结果数量") is None:
            return None, "结果数量尚未取得，缺值不能按已确认邻居数缓存；请重试探查填入明确数量（含显式 0）"
        entry, message = super().run_action(entry_id, action)
        # 启动探查与缓存结果都要保证两侧消费记录同源、可核查；丢弃缓存不产生交接。
        if action in ("启动探查", "缓存结果"):
            message += "；" + self._handoff(entry)
        return entry, message

    # ----- 同源交接：模板与推荐规则各只保留一份有效记录 -----
    def _handoff(self, entry: dict[str, Any]) -> str:
        source_code = str(entry.get("配置编号", ""))
        count = entry.get("结果数量")
        count_missing = count is None
        seed = str(entry.get("探查种子", ""))
        relation_filter = str(entry.get("关系过滤", ""))
        strategy = str(entry.get("扩展策略", "bfs"))
        depth = str(entry.get("深度限制", "3"))

        gs_entry, gs_duplicates = self._reconcile_graph_search(
            source_code, seed, relation_filter, strategy, depth, count_missing, count,
        )
        reco_entry, reco_duplicates = self._reconcile_reco_engine(
            source_code, seed, relation_filter, gs_entry["模板编号"], count_missing, count,
        )

        duplicate_total = gs_duplicates + reco_duplicates
        if count_missing:
            entry["交接状态"] = "数量待取得·草稿可续做"
            head = "邻边数量仍未得到（缺值，未当作零邻居），搜索模板与推荐规则保持草稿可续做"
        elif count == 0:
            entry["交接状态"] = "已确认空结果·两侧已交接"
            head = "已确认显式 0：真正的空子图，模板产出空结果、推荐无候选"
        else:
            entry["交接状态"] = "结果已确认·两侧已交接"
            head = f"已确认 {count} 条邻边，结果同步推荐待办作为候选取数依据"
        message = (
            f"{head}；搜索模板 {gs_entry['模板编号']}、推荐规则 {reco_entry['规则编号']}"
            " 携带同一来源配置编号可互相核查"
        )
        if duplicate_total:
            message += f"；另有 {duplicate_total} 份历史重复草稿标记异常，仅供追查"
        return message

    def _reconcile_graph_search(
        self,
        source_code: str,
        seed: str,
        relation_filter: str,
        strategy: str,
        depth: str,
        count_missing: bool,
        count: int | None,
    ) -> tuple[dict[str, Any], int]:
        rows = store.rows("graph_search")
        related = [
            row for row in rows
            if row.get("来源模块") == SOURCE_MODULE and row.get("来源配置") == source_code
        ]
        canonical = min(related, key=lambda row: int(row.get("id", 0)), default=None)
        duplicates = 0
        for extra in related:
            if extra is canonical:
                continue
            # 同源重试产生的多余消费记录：标记异常、保留为追查材料，不再参与有效交接。
            extra["abnormal"] = True
            extra["pending"] = True
            extra["交接状态"] = "重复草稿·仅供追查"
            duplicates += 1

        if canonical is None:
            next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            canonical = {
                "id": next_id,
                "status": "草稿",
                "pending": True,
                "abnormal": False,
                "来源模块": SOURCE_MODULE,
                "来源配置": source_code,
                "模板编号": f"GRAP-SUB-{next_id:04d}",
                "搜索类型": "subgraph-traversal",
                "查询语法": strategy,
                "起始实体": seed,
                "关系过滤": relation_filter,
                "跳数上限": depth,
                "排序规则": "relevance-desc",
                "使用频次": "0",
                "探查结论": "",
                "交接状态": "",
            }
            rows.append(canonical)
        else:
            # 重试刷新探查参数；已发布模板的人工状态不回退，只更新取数参数。
            canonical["查询语法"] = strategy
            canonical["起始实体"] = seed
            canonical["关系过滤"] = relation_filter
            canonical["跳数上限"] = depth
            canonical["abnormal"] = False

        if count_missing:
            canonical["探查结论"] = "邻边数量待取得（缺值，不可当作零邻居）"
            canonical["交接状态"] = "待确认·可采用种子与过滤续做"
        elif count == 0:
            canonical["探查结论"] = "已确认空结果：0 个邻边（空子图）"
            canonical["交接状态"] = "已确认空结果"
        else:
            canonical["探查结论"] = f"已确认 {count} 条邻边结果"
            canonical["交接状态"] = "结果已确认"
        return canonical, duplicates

    def _reconcile_reco_engine(
        self,
        source_code: str,
        seed: str,
        relation_filter: str,
        template_code: str,
        count_missing: bool,
        count: int | None,
    ) -> tuple[dict[str, Any], int]:
        rows = store.rows("reco_engine")
        related = [
            row for row in rows
            if row.get("来源模块") == SOURCE_MODULE and row.get("来源配置") == source_code
        ]
        canonical = min(related, key=lambda row: int(row.get("id", 0)), default=None)
        duplicates = 0
        for extra in related:
            if extra is canonical:
                continue
            extra["abnormal"] = True
            extra["pending"] = True
            extra["数据状态"] = "重复草稿·仅供追查"
            duplicates += 1

        if canonical is None:
            next_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            canonical = {
                "id": next_id,
                "status": "草稿",
                "pending": True,
                "abnormal": False,
                "来源模块": SOURCE_MODULE,
                "来源配置": source_code,
                "来源模板编号": template_code,
                "规则编号": f"RECO-SUB-{next_id:04d}",
                "推荐场景": "子图探查关联推荐",
                "源实体": seed,
                "推荐算法": "subgraph-neighbor-rank",
                "候选数量": "",
                "过滤条件": relation_filter,
                "命中次数": "0",
                "采纳率": "0%",
                "数据状态": "",
            }
            rows.append(canonical)
        else:
            canonical["来源模板编号"] = template_code
            canonical["过滤条件"] = relation_filter
            canonical["abnormal"] = False

        # 缺值不给候选数（区别于显式 0）；确认结果后同步到推荐待办。
        canonical["候选数量"] = "" if count_missing else int(count)
        if count_missing:
            canonical["数据状态"] = "待探查确认（候选数量缺值，非零候选）"
        elif count == 0:
            canonical["数据状态"] = "探查结果已同步：空子图，无推荐候选"
        else:
            canonical["数据状态"] = f"探查结果已同步：{int(count)} 个推荐候选取数依据"
        return canonical, duplicates

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["cross_module_consumers"] = ["graph_search", "reco_engine"]
        rows = store.rows(self.MODULE)
        base["missing_result_count"] = sum(1 for row in rows if row.get("结果数量") is None)
        base["confirmed_empty_subgraph"] = sum(1 for row in rows if row.get("结果数量") == 0)
        return base
