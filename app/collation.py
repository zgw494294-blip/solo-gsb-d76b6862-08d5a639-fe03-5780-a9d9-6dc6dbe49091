"""异文对读台：全局最小编辑距离对齐与异文汇总。

对齐规则
--------
- 匹配成本 0；替换、删除、插入成本均为 1。
- 并列（同为最小成本）路径按操作序列的字典序取舍，
  操作的字典序为：匹配 < 替换 < 删除 < 插入。
- 空白与标点一律照常参与比较，不做任何规范化。

坐标体系
--------
- 字符坐标：底本第 1 字 … 第 m 字（1 起计）。
- 间隙坐标：间隙 0（文首）… 间隙 m（文末），间隙 g 位于第 g 字之后、
  第 g+1 字之前，首尾间隙一并计入。
"""

OP_MATCH = "match"
OP_SUBSTITUTE = "substitute"
OP_DELETE = "delete"
OP_INSERT = "insert"

KIND_LABELS = {
    OP_MATCH: "相同",
    OP_SUBSTITUTE: "替换",
    OP_DELETE: "缺字",
    OP_INSERT: "插入",
}


class EditionError(ValueError):
    """校定选择无效（重复坐标、无效坐标或读法不属于该坐标）。"""


def _cost_table(base: str, witness: str):
    """最小编辑代价表：cost[i][j] = 对齐 base[i:] 与 witness[j:] 的最小成本。"""
    m, n = len(base), len(witness)
    cost = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        cost[i][n] = cost[i + 1][n] + 1
    for j in range(n - 1, -1, -1):
        cost[m][j] = cost[m][j + 1] + 1
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            diag = (0 if base[i] == witness[j] else 1) + cost[i + 1][j + 1]
            dele = 1 + cost[i + 1][j]
            ins = 1 + cost[i][j + 1]
            cost[i][j] = min(diag, dele, ins)
    return cost


def _viable_ops(base: str, witness: str, cost: list, i: int, j: int):
    """(i, j) 处能走通最优解的操作，按字典序（匹配＜替换＜删除＜插入）排列。"""
    m, n = len(base), len(witness)
    ops = []
    if i < m and j < n:
        if base[i] == witness[j] and cost[i][j] == cost[i + 1][j + 1]:
            ops.append(OP_MATCH)
        if cost[i][j] == 1 + cost[i + 1][j + 1]:
            ops.append(OP_SUBSTITUTE)
    if i < m and cost[i][j] == 1 + cost[i + 1][j]:
        ops.append(OP_DELETE)
    if j < n and cost[i][j] == 1 + cost[i][j + 1]:
        ops.append(OP_INSERT)
    return ops


def _step_event(op: str, i: int, j: int):
    """在 (i, j) 处执行操作，返回 (事件, 新 i, 新 j)。"""
    if op in (OP_MATCH, OP_SUBSTITUTE):
        return (op, i, j), i + 1, j + 1
    if op == OP_DELETE:
        return (op, i, None), i + 1, j
    return (op, None, j), i, j + 1


def _greedy_events(base: str, witness: str, cost: list):
    """自 (0, 0) 起贪心走最小成本路径：每步在“可走通最优解”的操作里
    选字典序最小者（匹配 < 替换 < 删除 < 插入），即得操作序列字典序
    最小的最优路径。"""
    m, n = len(base), len(witness)
    events = []
    i = j = 0
    while i < m or j < n:
        op = _viable_ops(base, witness, cost, i, j)[0]
        event, i, j = _step_event(op, i, j)
        events.append(event)
    return events


def align(base: str, witness: str):
    """把抄本全局对齐到底本，返回操作事件列表（自左向右）。

    每个事件为三元组 (op, i, j)：
      ("match",      i, j)  底本 base[i] 与抄本 witness[j] 相同
      ("substitute", i, j)  底本 base[i] 被抄本 witness[j] 替换
      ("delete",     i, None) 底本 base[i] 在抄本中缺字
      ("insert",     None, j) 抄本 witness[j] 为插入字
    i、j 均为 0 起索引。
    """
    return _greedy_events(base, witness, _cost_table(base, witness))


def _path_count(base: str, witness: str, cost: list) -> int:
    """精确统计最小代价操作路径数：不同操作序列算不同路径。"""
    m, n = len(base), len(witness)
    # count[i][j] = 自 (i, j) 起走完对齐的最优路径数
    count = [[0] * (n + 1) for _ in range(m + 1)]
    count[m][n] = 1
    for i in range(m, -1, -1):
        for j in range(n, -1, -1):
            if i == m and j == n:
                continue
            total = 0
            for op in _viable_ops(base, witness, cost, i, j):
                if op in (OP_MATCH, OP_SUBSTITUTE):
                    total += count[i + 1][j + 1]
                elif op == OP_DELETE:
                    total += count[i + 1][j]
                else:  # OP_INSERT
                    total += count[i][j + 1]
            count[i][j] = total
    return count[0][0]


def _second_events(base: str, witness: str, cost: list, first_events: list):
    """构造操作序列字典序严格次小的最优路径。

    首条路径每步都取字典序最小的可行操作；其他任何最优路径与首条路径
    的首次分叉处，必改取字典序更大的可行操作。分叉越晚、其后全取最小，
    所得序列字典序越小。故重走首条路径，在最晚一个存在次小可行操作的
    位置改取次小操作，其后逐步取最小，即得严格次小的最优路径。
    路径唯一时返回 None。
    """
    m, n = len(base), len(witness)
    states = []  # 首条路径每步的出发位置与可行操作
    i = j = 0
    for op, _a, _b in first_events:
        states.append((i, j, _viable_ops(base, witness, cost, i, j)))
        _event, i, j = _step_event(op, i, j)
    for t in range(len(states) - 1, -1, -1):
        si, sj, viable = states[t]
        if len(viable) < 2:
            continue
        events = list(first_events[:t])
        event, i, j = _step_event(viable[1], si, sj)
        events.append(event)
        while i < m or j < n:
            op = _viable_ops(base, witness, cost, i, j)[0]
            event, i, j = _step_event(op, i, j)
            events.append(event)
        return events
    return None


def _readings_from_events(base: str, witness: str, events: list):
    """按底本坐标整理一份抄本的读法。

    返回 (char_readings, gap_readings)：
      char_readings[i]：底本第 i+1 字处，该抄本的读法；
      gap_readings[g]：间隙 g 处，该抄本的插入情况。
    每条读法为 {"kind", "reading", "span"}，span 为抄本内的
    0 起半开区间 [start, end)；空区间表示抄本中的对应间隙位置。
    """
    m = len(base)
    char_readings = [None] * m
    gap_readings = [None] * (m + 1)

    i = j = 0
    opened_gap = -1  # 已记录到哪个间隙
    for op, a, b in events:
        if opened_gap < i:
            gap_readings[i] = {"kind": OP_MATCH, "reading": "", "span": [j, j]}
            opened_gap = i
        if op == OP_MATCH:
            char_readings[i] = {
                "kind": OP_MATCH,
                "reading": base[i],
                "span": [j, j + 1],
            }
            i += 1
            j += 1
        elif op == OP_SUBSTITUTE:
            char_readings[i] = {
                "kind": OP_SUBSTITUTE,
                "reading": witness[b],
                "span": [b, b + 1],
            }
            i += 1
            j += 1
        elif op == OP_DELETE:
            char_readings[i] = {"kind": OP_DELETE, "reading": "", "span": [j, j]}
            i += 1
        else:  # OP_INSERT
            gap = gap_readings[i]
            if gap["kind"] == OP_MATCH:
                gap["kind"] = OP_INSERT
                gap["reading"] = witness[b]
                gap["span"] = [b, b + 1]
            else:
                gap["reading"] += witness[b]
                gap["span"][1] = b + 1
            j += 1
    for g in range(opened_gap + 1, m + 1):
        gap_readings[g] = {"kind": OP_MATCH, "reading": "", "span": [j, j]}
    return char_readings, gap_readings


def _witness_readings(base: str, witness: str):
    """现行首条路径（align 的字典序取舍）下的抄本读法。"""
    return _readings_from_events(base, witness, align(base, witness))


def _gap_label(g: int, m: int) -> str:
    if g == 0:
        return "文首间隙"
    if g == m:
        return "文末间隙"
    return f"第{g}字后间隙"


def _iter_coordinates(base: str):
    """按「间隙0、第1字、间隙1、……、第m字、间隙m」的顺序遍历底本坐标。

    每项为 (type_, index, coordinate, base_reading, base_span)：
      间隙坐标 type_="gap"，底本读法为空串，base_span 为零长区间 [g, g)；
      字符坐标 type_="char"，底本读法为底本该字，base_span 为 [i, i+1)。
    index 即对外坐标序号：间隙为 0…m，字符为 1…m。
    """
    m = len(base)
    for g in range(m + 1):
        yield (
            "gap",
            g,
            {"type": "gap", "index": g, "label": _gap_label(g, m)},
            "",
            [g, g],
        )
        if g < m:
            yield (
                "char",
                g + 1,
                {"type": "char", "index": g + 1, "label": f"第{g + 1}字"},
                base[g],
                [g, g + 1],
            )


def collate(base: str, witnesses: list) -> dict:
    """汇总各抄本与底本的异文，返回完整对读结果。"""
    m = len(base)
    names = [w["name"] for w in witnesses]
    per_witness = [_witness_readings(base, w["text"]) for w in witnesses]

    entries = []

    def add_entry(coordinate: dict, base_reading: str, base_span: list, readings: list):
        groups = {}
        witness_spans = {}
        for name, r in zip(names, readings):
            group = groups.setdefault(
                r["reading"],
                {"reading": r["reading"], "kind": r["kind"], "witnesses": []},
            )
            group["witnesses"].append(name)
            witness_spans[name] = [r["span"]]
        diff_kinds = sorted(
            {g["kind"] for g in groups.values() if g["kind"] != OP_MATCH}
        )
        entries.append(
            {
                "id": len(entries),
                "coordinate": coordinate,
                "base_reading": base_reading,
                "diff_kinds": diff_kinds,
                "groups": list(groups.values()),
                "spans": {"base": [base_span], "witnesses": witness_spans},
            }
        )

    # 依坐标顺序扫描：间隙0、第1字、间隙1、第2字、……、第m字、间隙m
    for type_, _idx, coordinate, base_reading, base_span in _iter_coordinates(base):
        if type_ == "gap":
            g = coordinate["index"]
            readings = [pw[1][g] for pw in per_witness]
        else:
            i = coordinate["index"] - 1
            readings = [pw[0][i] for pw in per_witness]
        if any(r["kind"] != OP_MATCH for r in readings):
            add_entry(coordinate, base_reading, base_span, readings)

    return {
        "base": {"text": base, "length": m},
        "witnesses": [
            {"name": w["name"], "text": w["text"], "length": len(w["text"])}
            for w in witnesses
        ],
        "entries": entries,
        "stats": {
            "base_length": m,
            "witness_count": len(witnesses),
            "diff_positions": len(entries),
        },
    }


def provenance(base: str, witnesses: list, query: str) -> dict:
    """待鉴文本来源分段：求总分最低的完整来源路径。

    复用 align／_witness_readings 的校验后对齐结果，把待鉴文本与各抄本的
    读法一并映射到底本字符／间隙坐标。

    鉴别坐标：各抄本读法不全相同的坐标（待鉴读法本身不参与判定）。
    只在鉴别坐标上为待鉴读法指派一份来源抄本：
      待鉴读法与来源抄本相同计 0，否则计 1；
      相邻鉴别坐标来源不同（换源）另计 2。
    求总分最低的完整来源路径；总分相同时先取换源次数少者，再按抄本录入
    顺序的来源序列取字典序最小者（以录入序号比较）。

    返回 {"decidable", "discriminative_count", "score", "switches",
    "mismatches", "path", "segments"}；无鉴别坐标时 decidable=False。
    segments 把路径按连续同源的鉴别坐标归并成段，给出首尾坐标、
    来源抄本名与段内失配数。
    """
    names = [w["name"] for w in witnesses]
    per_witness = [_witness_readings(base, w["text"]) for w in witnesses]
    q_char, q_gap = _witness_readings(base, query)

    coords = []  # (coordinate, witness_readings, query_reading)
    for _type, _idx, coordinate, _br, _span in _iter_coordinates(base):
        if coordinate["type"] == "gap":
            g = coordinate["index"]
            witness_readings = [pw[1][g]["reading"] for pw in per_witness]
            query_reading = q_gap[g]["reading"]
        else:
            i = coordinate["index"] - 1
            witness_readings = [pw[0][i]["reading"] for pw in per_witness]
            query_reading = q_char[i]["reading"]
        # 鉴别坐标仅看抄本读法是否不全相同
        if len(set(witness_readings)) >= 2:
            coords.append((coordinate, witness_readings, query_reading))

    if not coords:
        return {
            "decidable": False,
            "discriminative_count": 0,
            "score": None,
            "switches": 0,
            "mismatches": 0,
            "path": [],
            "segments": [],
        }

    nw = len(witnesses)
    k = len(coords)

    # 动态规划：states[t][s] 为覆盖前 t+1 个鉴别坐标、最末来源为第 s 份
    # 抄本时的最优状态 (总分, 换源次数, 来源序号序列)。同一末来源下被
    # 逐项压倒的状态无需保留——后续代价只取决于末来源，而追加相同后缀
    # 不改变总分、换源次数的大小关系与序列字典序。
    _c0, wr0, qr0 = coords[0]
    states = [[(0 if qr0 == wr0[s] else 1, 0, (s,)) for s in range(nw)]]
    for t in range(1, k):
        _coord, wr, qr = coords[t]
        add = [0 if qr == wr[s] else 1 for s in range(nw)]
        layer = []
        for s in range(nw):
            best = None
            for p in range(nw):
                pt, psw, pseq = states[t - 1][p]
                switched = p != s
                cand = (
                    pt + add[s] + (2 if switched else 0),
                    psw + (1 if switched else 0),
                    pseq + (s,),
                )
                # 元组比较即「总分 → 换源次数 → 来源序列字典序」
                if best is None or cand < best:
                    best = cand
            layer.append(best)
        states.append(layer)

    score, switches, seq = min(states[k - 1][s] for s in range(nw))

    path = []
    for t, (coordinate, wr, qr) in enumerate(coords):
        s = seq[t]
        path.append(
            {
                "coordinate": {
                    "type": coordinate["type"],
                    "index": coordinate["index"],
                    "label": coordinate["label"],
                },
                "query_reading": qr,
                "source": names[s],
                "mismatch": 0 if qr == wr[s] else 1,
                "witness_readings": [
                    {
                        "name": names[w],
                        "reading": wr[w],
                        "matches_query": wr[w] == qr,
                    }
                    for w in range(nw)
                ],
            }
        )

    segments = []
    start = 0
    for t in range(1, k + 1):
        if t == k or seq[t] != seq[start]:
            end = t - 1
            segments.append(
                {
                    "start": dict(path[start]["coordinate"]),
                    "end": dict(path[end]["coordinate"]),
                    "witness": names[seq[start]],
                    "mismatches": sum(path[u]["mismatch"] for u in range(start, t)),
                }
            )
            start = t

    return {
        "decidable": True,
        "discriminative_count": k,
        "score": score,
        "switches": switches,
        "mismatches": sum(p["mismatch"] for p in path),
        "path": path,
        "segments": segments,
    }


def _mst(nw: int, coords: list, active: list):
    """在指定鉴别坐标上构造抄本最小生成树。

    coords 为 (coordinate, 各抄本读法) 列表，active 为参与计距的下标。
    两份抄本的距离＝这些坐标上读法不同的次数；候选边按「距离升序、
    再按两端录入序号（i 小者在前、再比 j）升序」排序后做 Kruskal 选边，
    并列时唯一定边。返回 [(distance, i, j), …]（i < j，选边顺序）。
    """
    edges = []
    for i in range(nw):
        for j in range(i + 1, nw):
            distance = 0
            for t in active:
                readings = coords[t][1]
                if readings[i] != readings[j]:
                    distance += 1
            edges.append((distance, i, j))
    edges.sort()  # 元组排序即 (距离, i, j)

    parent = list(range(nw))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    chosen = []
    for distance, i, j in edges:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
            chosen.append((distance, i, j))
            if len(chosen) == nw - 1:
                break
    return chosen


def relationships(base: str, witnesses: list) -> dict:
    """抄本异文距离树：鉴别坐标上的抄本间距与最小生成树。

    复用 align／_witness_readings 的对齐结果，只取各抄本读法不全相同的
    底本字符位与间隙（鉴别坐标）。两份抄本的距离为这些坐标上读法不同的
    次数；按距离升序、两端录入序号升序 Kruskal 选边，得到连接全部抄本的
    最小生成树。再逐一剔除每个鉴别坐标重算树：原树每条边标出被保留次数
    与鉴别坐标总数（保留比例即二者之商），并附两端读法不同的坐标明细。

    返回 {"decidable", "discriminative_count", "tree"}；无鉴别坐标时
    decidable=False、tree 为空（无法比较，不生成树）。
    """
    names = [w["name"] for w in witnesses]
    nw = len(witnesses)
    per_witness = [_witness_readings(base, w["text"]) for w in witnesses]

    coords = []  # (coordinate, 各抄本读法)
    for _type, _idx, coordinate, _br, _span in _iter_coordinates(base):
        if coordinate["type"] == "gap":
            readings = [pw[1][coordinate["index"]]["reading"] for pw in per_witness]
        else:
            readings = [pw[0][coordinate["index"] - 1]["reading"] for pw in per_witness]
        if len(set(readings)) >= 2:
            coords.append((coordinate, readings))

    if not coords:
        return {"decidable": False, "discriminative_count": 0, "tree": []}

    k = len(coords)
    tree = _mst(nw, coords, list(range(k)))

    # 逐一剔除一个鉴别坐标重算树，登记原树各边是否仍在
    jackknife = []
    for skip in range(k):
        jackknife.append({(i, j) for _d, i, j in
                          _mst(nw, coords, [t for t in range(k) if t != skip])})

    tree_out = []
    for distance, i, j in tree:
        differences = []
        for coordinate, readings in coords:
            if readings[i] != readings[j]:
                differences.append(
                    {
                        "coordinate": {
                            "type": coordinate["type"],
                            "index": coordinate["index"],
                            "label": coordinate["label"],
                        },
                        "a_reading": readings[i],
                        "b_reading": readings[j],
                    }
                )
        tree_out.append(
            {
                "a": {"index": i + 1, "name": names[i]},
                "b": {"index": j + 1, "name": names[j]},
                "distance": distance,
                "retained": sum((i, j) in edges for edges in jackknife),
                "total": k,
                "differences": differences,
            }
        )

    return {"decidable": True, "discriminative_count": k, "tree": tree_out}


def alignment_audit(base: str, witnesses: list) -> dict:
    """最优对齐路径审计：逐抄本统计最小代价路径数并比较次小路径。

    对每份抄本，沿用 align 的编辑代价（匹配 0；替换／删除／插入 1）
    精确统计所有最小代价操作路径——不同操作序列算不同路径，路径数
    以十进制字符串返回（可能极大）。按「匹配＜替换＜删除＜插入」的
    操作序列字典序，首条路径即 align 的取舍结果；路径数大于 1 时
    另求严格次小的路径，比较两条路径在底本字符与间隙坐标上的读法
    及抄本码点半开区间，列出有差别的坐标及两侧值（读法相同但区间
    不同的坐标同样列出）。

    返回 {"witnesses": [{"name", "path_count", "alternative"}]}；
    alternative 为 None（路径唯一）或 {"differences": [...]}。
    """
    results = []
    for w in witnesses:
        text = w["text"]
        cost = _cost_table(base, text)
        first_events = _greedy_events(base, text, cost)
        count = _path_count(base, text, cost)
        alternative = None
        if count > 1:
            second_events = _second_events(base, text, cost, first_events)
            first_readings = _readings_from_events(base, text, first_events)
            second_readings = _readings_from_events(base, text, second_events)
            differences = []
            for type_, idx, coordinate, _br, _span in _iter_coordinates(base):
                if type_ == "gap":
                    r_first = first_readings[1][idx]
                    r_second = second_readings[1][idx]
                else:
                    r_first = first_readings[0][idx - 1]
                    r_second = second_readings[0][idx - 1]
                if r_first != r_second:
                    differences.append(
                        {
                            "coordinate": coordinate,
                            "first": r_first,
                            "second": r_second,
                        }
                    )
            alternative = {"differences": differences}
        results.append(
            {
                "name": w["name"],
                "path_count": str(count),
                "alternative": alternative,
            }
        )
    return {"witnesses": results}


def _coord_desc(type_: str, index: int, m: int) -> str:
    if type_ == "char":
        return f"第{index}字"
    if index == 0:
        return "文首间隙（间隙0）"
    if index == m:
        return f"文末间隙（间隙{m}）"
    return f"第{index}字后间隙（间隙{index}）"


def _reading_prompt(reading: str, type_: str) -> str:
    if reading != "":
        return f"读法「{reading}」"
    return "缺字" if type_ == "char" else "无插入"


def edition(base: str, witnesses: list, selections: list) -> dict:
    """依校定选择拼成完整校定文本。

    selections 仅记录非默认选择，每条为 {"type", "index", "reading"}：
      type 为 "char"（字符坐标）或 "gap"（间隙坐标）；
      char 的 index 为 1…m，gap 的 index 为 0…m；
      reading 为底本读法或该坐标下某份抄本的实际读法。
    默认取底本读法：字符坐标保留底本该字，间隙坐标不插入任何文字。

    返回 {"text", "length", "coordinates"}：coordinates 依拼字顺序
    列出全部坐标，含所选读法、该读法在成稿中的 Unicode 码点半开区间
    [start, end) 及支持该读法的抄本名（底本读法附底本；缺字／无插入
    保留零长区间）。
    """
    m = len(base)
    names = [w["name"] for w in witnesses]
    per_witness = [_witness_readings(base, w["text"]) for w in witnesses]

    # 仅差异坐标允许出现在 selections 中：其余坐标不存在异文条目，
    # 底本读法是唯一可选读法。
    diffs = {}  # (type, index) -> {"base": 底本读法, "supports": {读法: [抄本名]}}
    for type_, idx, coordinate, base_reading, _span in _iter_coordinates(base):
        if type_ == "gap":
            rs = [pw[1][coordinate["index"]] for pw in per_witness]
        else:
            rs = [pw[0][coordinate["index"] - 1] for pw in per_witness]
        if any(r["kind"] != OP_MATCH for r in rs):
            supports = {}
            for name, r in zip(names, rs):
                supports.setdefault(r["reading"], []).append(name)
            diffs[(type_, idx)] = {"base": base_reading, "supports": supports}

    chosen = {}
    for sel in selections:
        key = (sel["type"], sel["index"])
        if key in chosen:
            raise EditionError(f"坐标重复：{_coord_desc(key[0], key[1], m)}")
        type_, index = key
        if type_ not in ("char", "gap"):
            raise EditionError(f"无效坐标类型：{type_}（仅支持 char／gap）")
        if type_ == "char" and not 1 <= index <= m:
            raise EditionError(f"无效字符坐标：第{index}字（底本共 {m} 字）")
        if type_ == "gap" and not 0 <= index <= m:
            raise EditionError(f"无效间隙坐标：间隙{index}（合法范围 0～{m}）")
        if key not in diffs:
            raise EditionError(f"{_coord_desc(type_, index, m)}不是差异条目，无可选读法")
        diff = diffs[key]
        if sel["reading"] not in diff["supports"]:
            raise EditionError(
                f"{_coord_desc(type_, index, m)}不存在{_reading_prompt(sel['reading'], type_)}，"
                "该读法不属于此坐标"
            )
        chosen[key] = sel["reading"]

    parts = []
    coordinates = []
    pos = 0
    for type_, idx, coordinate, base_reading, _span in _iter_coordinates(base):
        if (type_, idx) in chosen:
            reading = chosen[(type_, idx)]
            if (type_, idx) in diffs and reading == diffs[(type_, idx)]["base"]:
                supporters = ["底本", *diffs[(type_, idx)]["supports"][reading]]
            else:
                supporters = list(diffs[(type_, idx)]["supports"][reading])
            is_default = False
        else:
            reading = base_reading
            supporters = ["底本"]
            is_default = True
        start = pos
        pos += len(reading)
        coordinates.append(
            {
                "coordinate": {"type": coordinate["type"], "index": coordinate["index"],
                               "label": coordinate["label"]},
                "reading": reading,
                "default": is_default,
                "span": [start, pos],
                "supporters": supporters,
            }
        )
        parts.append(reading)

    return {
        "text": "".join(parts),
        "length": pos,
        "coordinates": coordinates,
    }
