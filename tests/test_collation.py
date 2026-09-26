"""对齐与校勘规则测试。

直接运行：python tests/test_collation.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.collation import (  # noqa: E402
    EditionError,
    align,
    alignment_audit,
    collate,
    edition,
    provenance,
    relationships,
)


def ops(events):
    return [e[0] for e in events]


def test_align_basic():
    assert ops(align("abc", "abc")) == ["match", "match", "match"]
    assert ops(align("abc", "axc")) == ["match", "substitute", "match"]
    assert ops(align("abc", "ac")) == ["match", "delete", "match"]
    assert ops(align("ac", "abc")) == ["match", "insert", "match"]
    # 空白与标点照常比较
    assert ops(align("a b", "a  b")) == ["match", "match", "insert", "match"]
    assert ops(align("a，b", "a。b")) == ["match", "substitute", "match"]


def test_tie_break():
    # 匹配 < 删除：删字落在最后
    assert ops(align("aa", "a")) == ["match", "delete"]
    # 匹配 < 插入：插字落在最后
    assert ops(align("a", "aa")) == ["match", "insert"]
    # 替换 < 删除/插入组合
    assert ops(align("ab", "ba")) == ["substitute", "substitute"]
    # 替换 < 删除
    assert ops(align("ab", "c")) == ["substitute", "delete"]
    # 单字不同：替换（成本 1）优于删除＋插入（成本 2）
    assert ops(align("a", "b")) == ["substitute"]


def test_collate_groups():
    res = collate(
        "天地人",
        [
            {"name": "甲", "text": "天地人"},
            {"name": "乙", "text": "天地間人"},
            {"name": "丙", "text": "天池人"},
            {"name": "丁", "text": "天人"},
        ],
    )
    assert res["stats"]["diff_positions"] == 2

    gap = [e for e in res["entries"] if e["coordinate"]["type"] == "gap"]
    assert len(gap) == 1 and gap[0]["coordinate"]["index"] == 2
    groups = {g["reading"]: g for g in gap[0]["groups"]}
    assert groups["間"]["witnesses"] == ["乙"] and groups["間"]["kind"] == "insert"
    assert groups[""]["witnesses"] == ["甲", "丙", "丁"]
    assert gap[0]["spans"]["witnesses"]["乙"] == [[2, 3]]
    assert gap[0]["spans"]["base"] == [[2, 2]]

    char = [e for e in res["entries"] if e["coordinate"]["type"] == "char"]
    assert len(char) == 1 and char[0]["coordinate"]["index"] == 2
    groups = {g["reading"]: g for g in char[0]["groups"]}
    assert groups["地"]["witnesses"] == ["甲", "乙"] and groups["地"]["kind"] == "match"
    assert groups["池"]["witnesses"] == ["丙"] and groups["池"]["kind"] == "substitute"
    assert groups[""]["witnesses"] == ["丁"] and groups[""]["kind"] == "delete"
    assert char[0]["spans"]["witnesses"]["丁"] == [[1, 1]]


def test_collate_edge_gaps():
    res = collate(
        "中",
        [
            {"name": "甲", "text": "前中"},
            {"name": "乙", "text": "中後"},
        ],
    )
    gaps = {e["coordinate"]["index"]: e for e in res["entries"]}
    assert set(gaps) == {0, 1}
    assert gaps[0]["coordinate"]["label"] == "文首间隙"
    assert gaps[1]["coordinate"]["label"] == "文末间隙"
    g0 = {g["reading"]: g["witnesses"] for g in gaps[0]["groups"]}
    g1 = {g["reading"]: g["witnesses"] for g in gaps[1]["groups"]}
    assert g0["前"] == ["甲"] and g0[""] == ["乙"]
    assert g1["後"] == ["乙"] and g1[""] == ["甲"]


def test_no_diff():
    res = collate("相同文本", [{"name": "甲", "text": "相同文本"}, {"name": "乙", "text": "相同文本"}])
    assert res["entries"] == []


W = [
    {"name": "甲", "text": "天地人"},
    {"name": "乙", "text": "天地間人"},
    {"name": "丙", "text": "天池人"},
    {"name": "丁", "text": "天人"},
]


def test_edition_default_is_base():
    # 无选择时校定稿即底本；全部坐标按「间隙0、第1字……间隙m」返回
    res = edition("天地人", W, [])
    assert res["text"] == "天地人"
    assert res["length"] == 3
    types = [(c["coordinate"]["type"], c["coordinate"]["index"]) for c in res["coordinates"]]
    assert types == [
        ("gap", 0), ("char", 1), ("gap", 1), ("char", 2),
        ("gap", 2), ("char", 3), ("gap", 3),
    ]
    assert all(c["default"] for c in res["coordinates"])
    assert [c["span"] for c in res["coordinates"]] == [
        [0, 0], [0, 1], [1, 1], [1, 2], [2, 2], [2, 3], [3, 3],
    ]
    assert all(c["supporters"] == ["底本"] for c in res["coordinates"])


def test_edition_choose_insert():
    # 间隙2取乙本插入「間」，其余依底本 → 天地間人
    res = edition("天地人", W, [{"type": "gap", "index": 2, "reading": "間"}])
    assert res["text"] == "天地間人"
    by = {(c["coordinate"]["type"], c["coordinate"]["index"]): c for c in res["coordinates"]}
    assert by[("gap", 2)]["span"] == [2, 3]
    assert by[("gap", 2)]["supporters"] == ["乙"]
    assert by[("gap", 2)]["default"] is False
    # 后续字符坐标在成稿中整体后移
    assert by[("char", 3)]["span"] == [3, 4]
    assert by[("char", 3)]["supporters"] == ["底本"]
    # 间隙0、1仍为零长区间
    assert by[("gap", 0)]["span"] == [0, 0]
    assert by[("gap", 1)]["span"] == [1, 1]


def test_edition_choose_substitute_and_delete():
    # 第2字取丙本「池」→ 天池人
    res = edition("天地人", W, [{"type": "char", "index": 2, "reading": "池"}])
    assert res["text"] == "天池人"
    c2 = [c for c in res["coordinates"] if c["coordinate"] == {"type": "char", "index": 2, "label": "第2字"}][0]
    assert c2["span"] == [1, 2] and c2["supporters"] == ["丙"]

    # 第2字取丁本缺字（空读法）→ 天人，零长区间保留
    res = edition("天地人", W, [{"type": "char", "index": 2, "reading": ""}])
    assert res["text"] == "天人"
    assert res["length"] == 2
    c2 = [c for c in res["coordinates"] if c["coordinate"]["index"] == 2
          and c["coordinate"]["type"] == "char"][0]
    assert c2["span"] == [1, 1] and c2["reading"] == ""
    assert c2["supporters"] == ["丁"]
    # 第3字坐标在成稿中仍位于位置 1
    c3 = [c for c in res["coordinates"] if c["coordinate"] == {"type": "char", "index": 3, "label": "第3字"}][0]
    assert c3["span"] == [1, 2]


def test_edition_choose_base_reading_lists_base_and_witnesses():
    # 在差异坐标显式选底本读法时，支持者含底本与同读法的抄本
    res = edition("天地人", W, [{"type": "char", "index": 2, "reading": "地"}])
    c2 = [c for c in res["coordinates"] if c["coordinate"]["type"] == "char"
          and c["coordinate"]["index"] == 2][0]
    assert c2["supporters"] == ["底本", "甲", "乙"]
    assert c2["default"] is False


def test_edition_no_insert_on_insertion_gap():
    # 间隙2显式选「无插入」（空读法，即该间隙的底本读法）：
    # 支持者含底本与无插入的抄本
    res = edition("天地人", W, [{"type": "gap", "index": 2, "reading": ""}])
    assert res["text"] == "天地人"
    g2 = [c for c in res["coordinates"] if c["coordinate"]["type"] == "gap"
          and c["coordinate"]["index"] == 2][0]
    assert g2["span"] == [2, 2]
    assert g2["supporters"] == ["底本", "甲", "丙", "丁"]


def test_edition_edge_gaps():
    wits = [
        {"name": "甲", "text": "前中"},
        {"name": "乙", "text": "中後"},
    ]
    res = edition("中", wits, [
        {"type": "gap", "index": 0, "reading": "前"},
        {"type": "gap", "index": 1, "reading": "後"},
    ])
    assert res["text"] == "前中後"
    spans = {
        (c["coordinate"]["type"], c["coordinate"]["index"]): c["span"]
        for c in res["coordinates"]
    }
    assert spans[("gap", 0)] == [0, 1]
    assert spans[("char", 1)] == [1, 2]
    assert spans[("gap", 1)] == [2, 3]


def test_edition_errors():
    def expect_error(sels):
        try:
            edition("天地人", W, sels)
        except EditionError:
            return
        raise AssertionError("应当抛出 EditionError")

    # 重复坐标
    expect_error([
        {"type": "gap", "index": 2, "reading": "間"},
        {"type": "gap", "index": 2, "reading": ""},
    ])
    # 无效字符坐标
    expect_error([{"type": "char", "index": 4, "reading": "x"}])
    # 无效间隙坐标
    expect_error([{"type": "gap", "index": 4, "reading": "x"}])
    # 非差异坐标（第1字全部相同）
    expect_error([{"type": "char", "index": 1, "reading": "天"}])
    # 读法不属于该坐标：「池」只出现在第2字
    expect_error([{"type": "char", "index": 3, "reading": "池"}])
    # 字符坐标不接受任何抄本都没有的读法
    expect_error([{"type": "char", "index": 2, "reading": "玄"}])
    # 无效坐标类型
    expect_error([{"type": "word", "index": 1, "reading": "天"}])


def test_edition_no_diff_inputs():
    # 完全一致时不接受任何选择，空选择给出底本全文
    res = edition("相同文本",
                  [{"name": "甲", "text": "相同文本"}, {"name": "乙", "text": "相同文本"}],
                  [])
    assert res["text"] == "相同文本" and len(res["coordinates"]) == 9
    try:
        edition("相同文本",
                [{"name": "甲", "text": "相同文本"}, {"name": "乙", "text": "相同文本"}],
                [{"type": "char", "index": 1, "reading": "相"}])
    except EditionError:
        pass
    else:
        raise AssertionError("非差异坐标应当被拒绝")


def test_provenance_lexicographic_tie():
    # 单抄本路径同分（各失配 1 处、无换源）：取录入顺序靠前的来源
    res = provenance(
        "abcdef",
        [
            {"name": "甲", "text": "Xbcdef"},  # 第1字与待鉴不同，其余相同
            {"name": "乙", "text": "abcdeX"},  # 第6字与待鉴不同，其余相同
        ],
        "abcdef",
    )
    assert res["decidable"] is True
    assert res["discriminative_count"] == 2
    assert res["score"] == 1 and res["switches"] == 0 and res["mismatches"] == 1
    assert [p["source"] for p in res["path"]] == ["甲", "甲"]
    assert len(res["segments"]) == 1
    seg = res["segments"][0]
    assert (seg["start"]["type"], seg["start"]["index"]) == ("char", 1)
    assert (seg["end"]["type"], seg["end"]["index"]) == ("char", 6)
    assert seg["witness"] == "甲" and seg["mismatches"] == 1


def test_provenance_lexicographic_tie_other_order():
    # 抄本录入顺序对调后，同分改取另一份（字典序随录入顺序）
    res = provenance(
        "abcdef",
        [
            {"name": "乙", "text": "abcdeX"},
            {"name": "甲", "text": "Xbcdef"},
        ],
        "abcdef",
    )
    assert res["score"] == 1 and res["switches"] == 0
    assert [p["source"] for p in res["path"]] == ["乙", "乙"]


def test_provenance_switching_wins():
    # 两半各接近一份抄本：一次换源（2 分）抵两处失配（2 分）以上
    res = provenance(
        "abcdef",
        [
            {"name": "甲", "text": "abcYYY"},
            {"name": "乙", "text": "XXXdef"},
        ],
        "abcdef",
    )
    # 单走甲／乙各失配 3 处（3 分）；甲→乙：失配 0、换源 1 次（2 分）
    assert res["score"] == 2 and res["switches"] == 1 and res["mismatches"] == 0
    assert [p["source"] for p in res["path"]] == ["甲", "甲", "甲", "乙", "乙", "乙"]
    assert [s["witness"] for s in res["segments"]] == ["甲", "乙"]
    first, second = res["segments"]
    assert (first["start"]["index"], first["end"]["index"]) == (1, 3)
    assert (second["start"]["index"], second["end"]["index"]) == (4, 6)
    assert first["mismatches"] == 0 and second["mismatches"] == 0


def test_provenance_fewer_switches_wins_on_tie():
    # 总分相同：0 换源（失配 2）胜 1 换源（失配 0 但换源 2 分）
    res = provenance(
        "abcdef",
        [
            {"name": "甲", "text": "XXcdef"},
            {"name": "乙", "text": "abXXXX"},
        ],
        "abcdef",
    )
    assert res["score"] == 2 and res["mismatches"] == 2 and res["switches"] == 0
    assert all(p["source"] == "甲" for p in res["path"])
    assert len(res["segments"]) == 1
    assert res["segments"][0]["mismatches"] == 2


def test_provenance_lexicographic_tie_same_switches():
    # 鉴别坐标前 3 处宜归甲、后 3 处宜归乙；两个换源方向总分均为 2
    # （失配 0 ＋换源 2）、换源次数相同 → 按来源序列字典序取甲→乙。
    # 第7字两份抄本读法相同（都与待鉴一致），不是鉴别坐标，不参与。
    res = provenance(
        "abcdefg",
        [
            {"name": "甲", "text": "abcYYYg"},
            {"name": "乙", "text": "XXXdefg"},
        ],
        "abcdefg",
    )
    assert res["discriminative_count"] == 6
    assert res["score"] == 2 and res["switches"] == 1 and res["mismatches"] == 0
    assert [p["source"] for p in res["path"]] == [
        "甲", "甲", "甲", "乙", "乙", "乙",
    ]
    # 反向路径（乙→甲）同分、同换源次数，但来源序列字典序更大
    witnesses = [seg["witness"] for seg in res["segments"]]
    assert witnesses == ["甲", "乙"]
    first, second = res["segments"]
    assert (first["start"]["index"], first["end"]["index"]) == (1, 3)
    assert (second["start"]["index"], second["end"]["index"]) == (4, 6)
    assert first["mismatches"] == 0 and second["mismatches"] == 0


def test_provenance_gap_coordinates():
    # 插入读法落在间隙坐标：待鉴即甲本，两处鉴别间隙都归甲
    res = provenance(
        "中",
        [
            {"name": "甲", "text": "前中"},
            {"name": "乙", "text": "中後"},
        ],
        "前中",
    )
    # 鉴别坐标为间隙0 与间隙1；字符「中」两份读法相同，不鉴别
    kinds = [(p["coordinate"]["type"], p["coordinate"]["index"]) for p in res["path"]]
    assert kinds == [("gap", 0), ("gap", 1)]
    assert [p["source"] for p in res["path"]] == ["甲", "甲"]
    assert res["score"] == 0 and res["switches"] == 0 and res["mismatches"] == 0
    seg = res["segments"][0]
    assert seg["start"] == {"type": "gap", "index": 0, "label": "文首间隙"}
    assert seg["end"] == {"type": "gap", "index": 1, "label": "文末间隙"}
    assert seg["witness"] == "甲" and seg["mismatches"] == 0


def test_provenance_gap_switching_wins():
    # 六个鉴别坐标全是间隙：前三个插入宜归甲、后三个宜归乙，
    # 一次换源（2 分）胜过任一份抄本的 3 处失配（3 分）
    res = provenance(
        "abcde",
        [
            {"name": "甲", "text": "XaYbZcde"},   # 间隙0/1/2 插入
            {"name": "乙", "text": "abcPdQeR"},   # 间隙3/4/5 插入
        ],
        "XaYbZcPdQeR",
    )
    kinds = [(p["coordinate"]["type"], p["coordinate"]["index"]) for p in res["path"]]
    assert kinds == [("gap", g) for g in range(6)]
    assert res["score"] == 2 and res["switches"] == 1 and res["mismatches"] == 0
    assert [p["source"] for p in res["path"]] == [
        "甲", "甲", "甲", "乙", "乙", "乙",
    ]
    first, second = res["segments"]
    assert first["start"]["label"] == "文首间隙"
    assert first["end"]["label"] == "第2字后间隙"
    assert second["start"]["label"] == "第3字后间隙"
    assert second["end"]["label"] == "文末间隙"
    assert [s["witness"] for s in res["segments"]] == ["甲", "乙"]


def test_provenance_non_discriminative_coordinates_ignored():
    # 第1字两份抄本同为 X（且待鉴也为 X）：读法虽都异于底本，
    # 但抄本之间读法相同，不属于鉴别坐标，不计来源
    res = provenance(
        "ab",
        [
            {"name": "甲", "text": "Xb"},
            {"name": "乙", "text": "XY"},
        ],
        "Xb",
    )
    # 仅第2字为鉴别坐标：甲读 b（同待鉴，0），乙读 Y（失配）
    assert res["discriminative_count"] == 1
    coord = res["path"][0]["coordinate"]
    assert coord == {"type": "char", "index": 2, "label": "第2字"}
    assert res["path"][0]["source"] == "甲"
    assert res["path"][0]["mismatch"] == 0
    assert res["score"] == 0 and res["switches"] == 0


def test_provenance_undecidable():
    res = provenance(
        "天地人",
        [
            {"name": "甲", "text": "天地人"},
            {"name": "乙", "text": "天地人"},
        ],
        "天地人",
    )
    assert res["decidable"] is False
    assert res["discriminative_count"] == 0
    assert res["score"] is None
    assert res["path"] == [] and res["segments"] == []


def test_relationships_basic_tree_and_jackknife():
    # 鉴别坐标：第1字（甲同底本「天」，乙丙作「大」）、第3字（丙作「入」）。
    # 距离：甲乙 1，乙丙 1，甲丙 2 → 树为 甲—乙、乙—丙。
    # 剔除第1字后：乙丙仍 1 相连，甲以 0 距边连乙（或丙，按序号取乙），
    # 即原边「甲乙」保留而「乙丙」也保留；剔除第3字后甲丙距离变 1，
    # 但树不变。故甲乙保留 2/2，乙丙保留 1/2。
    res = relationships(
        "天地人",
        [
            {"name": "甲", "text": "天地人"},
            {"name": "乙", "text": "大地人"},
            {"name": "丙", "text": "大地入"},
        ],
    )
    assert res["decidable"] is True
    assert res["discriminative_count"] == 2
    assert len(res["tree"]) == 2
    e1, e2 = res["tree"]
    assert (e1["a"]["name"], e1["b"]["name"], e1["distance"]) == ("甲", "乙", 1)
    assert (e1["retained"], e1["total"]) == (2, 2)
    assert (e2["a"]["name"], e2["b"]["name"], e2["distance"]) == ("乙", "丙", 1)
    assert (e2["retained"], e2["total"]) == (1, 2)

    # 点击树边：两端读法不同的坐标明细
    diffs = {(d["coordinate"]["type"], d["coordinate"]["index"]): d for d in e1["differences"]}
    assert set(diffs) == {("char", 1)}
    assert diffs[("char", 1)]["a_reading"] == "天"
    assert diffs[("char", 1)]["b_reading"] == "大"
    diffs2 = {(d["coordinate"]["type"], d["coordinate"]["index"]) for d in e2["differences"]}
    assert diffs2 == {("char", 3)}


def test_relationships_gap_coordinates():
    # 鉴别坐标全部为间隙：甲文首插「前」，乙文末插「後」，丙不插。
    # 距离：甲丙 1、乙丙 1、甲乙 2 → 边 甲—丙（1）、丙—乙（1）
    res = relationships(
        "中",
        [
            {"name": "甲", "text": "前中"},
            {"name": "乙", "text": "中後"},
            {"name": "丙", "text": "中"},
        ],
    )
    assert res["discriminative_count"] == 2
    edges = [(e["a"]["name"], e["b"]["name"], e["distance"]) for e in res["tree"]]
    assert edges == [("甲", "丙", 1), ("乙", "丙", 1)]
    first = res["tree"][0]
    gap0 = [d for d in first["differences"] if d["coordinate"]["index"] == 0][0]
    assert gap0["coordinate"]["label"] == "文首间隙"
    assert gap0["a_reading"] == "前" and gap0["b_reading"] == ""


def test_relationships_index_tiebreak():
    # 甲在三处都同底本，乙丙丁各异一处；甲—乙＝甲—丙＝甲—丁＝2，
    # 乙—丙＝乙—丁＝丙—丁＝2：全部边距同为 2 → 按两端录入序号
    # 升序取 甲乙、甲丙、甲丁。
    res = relationships(
        "abcd",
        [
            {"name": "甲", "text": "abcd"},
            {"name": "乙", "text": "Xbcd"},  # 异第1字
            {"name": "丙", "text": "aXcd"},  # 异第2字
            {"name": "丁", "text": "abXd"},  # 异第3字
        ],
    )
    assert res["discriminative_count"] == 3
    edges = [(e["a"]["name"], e["b"]["name"]) for e in res["tree"]]
    assert edges == [("甲", "乙"), ("甲", "丙"), ("甲", "丁")]
    # 4 份抄本恰 3 条边；两端录入序号从 1 起
    assert [e["a"]["index"] for e in res["tree"]] == [1, 1, 1]
    assert [e["b"]["index"] for e in res["tree"]] == [2, 3, 4]


def test_relationships_zero_distance_edge():
    # 乙、丙读法完全相同，但都与甲不同：存在距离 0 的边且应最先入选
    res = relationships(
        "ab",
        [
            {"name": "甲", "text": "ab"},
            {"name": "乙", "text": "XX"},
            {"name": "丙", "text": "XX"},
        ],
    )
    assert res["discriminative_count"] == 2
    e1, e2 = res["tree"]
    assert (e1["a"]["name"], e1["b"]["name"], e1["distance"]) == ("乙", "丙", 0)
    assert e1["differences"] == []
    assert (e1["retained"], e1["total"]) == (2, 2)
    assert e2["distance"] == 2


def test_relationships_two_witnesses():
    # 两份抄本只有一条边；k 处鉴别、保留 k/k
    res = relationships(
        "abc",
        [
            {"name": "甲", "text": "abc"},
            {"name": "乙", "text": "aXc"},
        ],
    )
    assert res["discriminative_count"] == 1
    assert len(res["tree"]) == 1
    edge = res["tree"][0]
    assert (edge["a"]["name"], edge["b"]["name"], edge["distance"]) == ("甲", "乙", 1)
    assert (edge["retained"], edge["total"]) == (1, 1)
    assert len(edge["differences"]) == 1
    assert edge["differences"][0]["coordinate"]["label"] == "第2字"


def test_relationships_undecidable():
    # 各抄本读法在所有坐标上完全相同（即使都异于底本）：无法比较，不生成树
    res = relationships(
        "天地人",
        [
            {"name": "甲", "text": "天池人"},
            {"name": "乙", "text": "天池人"},
        ],
    )
    assert res["decidable"] is False
    assert res["discriminative_count"] == 0
    assert res["tree"] == []


def test_alignment_audit_unique_path():
    # 完全一致：仅 1 条最优路径，无替代路径
    res = alignment_audit("abc", [{"name": "甲", "text": "abc"}])
    w = res["witnesses"][0]
    assert w["name"] == "甲"
    assert w["path_count"] == "1"
    assert w["alternative"] is None


def test_alignment_audit_substitute_vs_delete_insert():
    # base="ab"、抄本="ba"：最优代价 2，共 3 条路径
    # [替换,替换] ＜ [删除,匹配,插入] ＜ [插入,匹配,删除]
    res = alignment_audit("ab", [{"name": "甲", "text": "ba"}])
    w = res["witnesses"][0]
    assert w["path_count"] == "3"
    diffs = {(d["coordinate"]["type"], d["coordinate"]["index"]): d
             for d in w["alternative"]["differences"]}
    # 首条 [替换,替换] 与次小 [删除,匹配,插入]：两字读法全变，
    # 间隙1 读法同为空但抄本区间不同，间隙2 由无插入变为插入
    assert set(diffs) == {("char", 1), ("gap", 1), ("char", 2), ("gap", 2)}
    assert diffs[("char", 1)]["first"] == {
        "kind": "substitute", "reading": "b", "span": [0, 1]}
    assert diffs[("char", 1)]["second"] == {
        "kind": "delete", "reading": "", "span": [0, 0]}
    assert diffs[("gap", 1)]["first"] == {
        "kind": "match", "reading": "", "span": [1, 1]}
    assert diffs[("gap", 1)]["second"] == {
        "kind": "match", "reading": "", "span": [0, 0]}
    assert diffs[("char", 2)]["first"] == {
        "kind": "substitute", "reading": "a", "span": [1, 2]}
    assert diffs[("char", 2)]["second"] == {
        "kind": "match", "reading": "b", "span": [0, 1]}
    assert diffs[("gap", 2)]["first"] == {
        "kind": "match", "reading": "", "span": [2, 2]}
    assert diffs[("gap", 2)]["second"] == {
        "kind": "insert", "reading": "a", "span": [1, 2]}


def test_alignment_audit_second_path_late_fork():
    # base="aaa"、抄本="aa"：删除可落在 3 个位置，共 3 条路径；
    # 首步只有匹配可行，分叉发生在第二步
    # [匹配,匹配,删除] ＜ [匹配,删除,匹配] ＜ [删除,匹配,匹配]
    res = alignment_audit("aaa", [{"name": "甲", "text": "aa"}])
    w = res["witnesses"][0]
    assert w["path_count"] == "3"
    diffs = {(d["coordinate"]["type"], d["coordinate"]["index"]): d
             for d in w["alternative"]["differences"]}
    assert set(diffs) == {("char", 2), ("gap", 2), ("char", 3)}
    assert diffs[("char", 2)]["first"] == {
        "kind": "match", "reading": "a", "span": [1, 2]}
    assert diffs[("char", 2)]["second"] == {
        "kind": "delete", "reading": "", "span": [1, 1]}
    assert diffs[("char", 3)]["first"] == {
        "kind": "delete", "reading": "", "span": [2, 2]}
    assert diffs[("char", 3)]["second"] == {
        "kind": "match", "reading": "a", "span": [1, 2]}
    # 间隙2 读法同为无插入，但对应抄本区间不同
    assert diffs[("gap", 2)]["first"] == {
        "kind": "match", "reading": "", "span": [2, 2]}
    assert diffs[("gap", 2)]["second"] == {
        "kind": "match", "reading": "", "span": [1, 1]}


def test_alignment_audit_path_count_is_decimal_string():
    # 10 个相同字对齐 5 个相同字：5 匹配＋5 删除的任意排列，C(10,5)=252
    res = alignment_audit("a" * 10, [{"name": "甲", "text": "a" * 5}])
    w = res["witnesses"][0]
    assert isinstance(w["path_count"], str)
    assert w["path_count"] == "252"
    assert w["alternative"] is not None


def test_alignment_audit_per_witness():
    # 逐抄本各自统计；唯一插入位置的抄本只有 1 条路径
    res = alignment_audit(
        "天地人",
        [
            {"name": "甲", "text": "天地人"},
            {"name": "乙", "text": "天地間人"},
        ],
    )
    assert [w["name"] for w in res["witnesses"]] == ["甲", "乙"]
    assert res["witnesses"][0]["path_count"] == "1"
    assert res["witnesses"][0]["alternative"] is None
    assert res["witnesses"][1]["path_count"] == "1"
    assert res["witnesses"][1]["alternative"] is None


def run_all():
    for name, fn in sorted(
        ((k, v) for k, v in globals().items() if k.startswith("test_")),
        key=lambda kv: kv[0],
    ):
        fn()
        print(f"PASS {name}")
    print("全部测试通过。")


if __name__ == "__main__":
    run_all()
