#!/usr/bin/env python3
"""反重力 (Antigravity) × Claude Desktop 双端统一会话管理与 1:1 镜像共研引擎。
全程纯本地脚本，不调模型、不花 1 个 token！

核心能力：
  1. 双端统一「领域 · 主题」命名体系（共 38 个主线程，Claude 与反重力侧边栏 1:1 完全镜像对齐）。
  2. 双端共用同一份交接单 `~/.claude/session-handoffs/<线程名>.auto.md`（自动融合 Claude 端与反重力端最新进展，按最新活跃时间置顶）。
  3. 跨端 1:1 侧边栏镜像种子注入：Claude 独有的线程自动在反重力生成带最新交接单的 2 步原生会话（点击即可直接续写；一旦在反重力发消息，自动升级为双端共研线程）。
  4. 内核级防回写锁 (`原子写入`)：写入 `agyhub_summaries_proto.pb` 后加 `UF_IMMUTABLE` 锁，拦截 `language_server` 退出时 `fs.Path.AtomicWrite` (`os.Rename`) 的旧内存覆盖。

用法：
  agy-sessions                       查看双端统一主线程状态、上下文成本、跨端角色与计划改动（只看不改）
  agy-sessions --apply               执行改名 + 去重归档 + 跨端 1:1 侧边栏镜像注入 + 刷新双端交接单
  agy-sessions brief <标题片段或ID>   零 token 刷新该线程的双端合并交接摘要
  agy-sessions brief-all             批量刷新全部活跃主线程的交接摘要与 MANIFEST
  agy-sessions keep <标题片段>        设为常驻保留
  agy-sessions hide <标题片段>        设为归档隐藏
  agy-sessions rename <原名> <新名>   添加标题合并/重命名规则
  agy-sessions auto                  后台定时同步 + 刷新交接单 + 清理过期备份
"""
import datetime, glob, json, os, re, shutil, sqlite3, stat, subprocess, sys, time, uuid

AGY_DIR = os.path.expanduser("~/.gemini/antigravity")
ANNOT_DIR = os.path.join(AGY_DIR, "annotations")
CONV_DIR = os.path.join(AGY_DIR, "conversations")
BRAIN_DIR = os.path.join(AGY_DIR, "brain")
PROTO_PATH = os.path.join(AGY_DIR, "agyhub_summaries_proto.pb")
DB_PATH = os.path.join(AGY_DIR, "conversation_summaries.db")
RULES_PATH = os.path.join(AGY_DIR, "session-groups.json")
HANDOFFS_DIR = os.path.join(AGY_DIR, "session-handoffs")
CLAUDE_HANDOFFS_DIR = os.path.expanduser("~/.claude/session-handoffs")
AGY_MANIFEST_PATH = os.path.join(HANDOFFS_DIR, "AGY_MANIFEST.json")
CLAUDE_MANIFEST_PATH = os.path.join(CLAUDE_HANDOFFS_DIR, "CLAUDE_MANIFEST.json")
BACKUPS_DIR = os.path.join(AGY_DIR, "session-backups")

CHEAP_K, PRICEY_K = 80, 200

# 模板会话 ID（用于生成 1:1 跨端镜像的 2 步原生 SQLite 会话）
TPL_ANS_CID = "0836b13f-6f31-4f7d-969f-e11610b0da96"
TPL_ANS_TID = "87ee9910-528e-4802-a59e-2571d8d4de3d"
TPL_EEO_CID = "5c242037-38eb-4fda-a144-64742a8475d1"


def mirror_cid_for(g):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"agy-mirror-cid:{g}"))


def mirror_tid_for(g):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"agy-mirror-tid:{g}"))


# ---------------- Protobuf Wire Helpers ----------------

def read_varint(buf, pos):
    res, shift = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7


def write_varint(val):
    if val < 0:
        val += 1 << 64
    out = bytearray()
    while True:
        byte = val & 0x7F
        val >>= 7
        if val:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def parse_pb_fields(buf):
    pos = 0
    fields = []
    while pos < len(buf):
        tag_wire, pos = read_varint(buf, pos)
        fnum = tag_wire >> 3
        wtype = tag_wire & 7
        if wtype == 0:
            val, pos = read_varint(buf, pos)
        elif wtype == 1:
            val = buf[pos : pos + 8]
            pos += 8
        elif wtype == 2:
            length, pos = read_varint(buf, pos)
            val = buf[pos : pos + length]
            pos += length
        elif wtype == 5:
            val = buf[pos : pos + 4]
            pos += 4
        else:
            raise ValueError(f"Unsupported wire type {wtype}")
        fields.append((fnum, wtype, val))
    return fields


def encode_pb_fields(fields):
    out = bytearray()
    for fnum, wtype, val in fields:
        out += write_varint((fnum << 3) | wtype)
        if wtype == 0:
            out += write_varint(val)
        elif wtype in (1, 5):
            out += val
        elif wtype == 2:
            out += write_varint(len(val)) + val
    return bytes(out)


def parse_summary_pb_state(raw_bytes):
    if not raw_bytes:
        return None, False
    try:
        fields = parse_pb_fields(raw_bytes)
    except Exception:
        return None, False
    f1_title = None
    f15_title = None
    archived = False
    for fnum, wtype, val in fields:
        if fnum == 1 and wtype == 2:
            f1_title = val.decode("utf-8", errors="ignore")
        elif fnum == 15 and wtype == 2 and val:
            try:
                sub = parse_pb_fields(val)
                for sfn, swt, sv in sub:
                    if sfn == 1 and swt == 2:
                        f15_title = sv.decode("utf-8", errors="ignore")
                    elif sfn == 4 and swt == 0 and sv == 1:
                        archived = True
            except Exception:
                pass
    return (f15_title or f1_title), archived


def encode_timestamp(sec, nanos=100000000):
    return encode_pb_fields([(1, 0, int(sec)), (2, 0, int(nanos))])


def update_summary_pb(raw_bytes, cid, title, archived, prev_annot, now_sec, override_mtime=None):
    fields = parse_pb_fields(raw_bytes) if raw_bytes else []
    title_bytes = title.encode("utf-8")
    new_fields = []
    found_f1 = False
    found_f4 = False
    found_f15 = False

    vsec = int(override_mtime) if override_mtime else (prev_annot.get("view_sec") or int(now_sec))
    vnanos = prev_annot.get("view_nanos") or 100000000
    asec = prev_annot.get("arch_sec") or int(now_sec)
    ananos = prev_annot.get("arch_nanos") or 100000000

    for fnum, wtype, val in fields:
        if fnum == 1 and wtype == 2:
            if not found_f1:
                new_fields.append((1, 2, title_bytes))
                found_f1 = True
        elif fnum in (3, 7, 10) and wtype == 2 and override_mtime:
            new_fields.append((fnum, 2, encode_timestamp(int(override_mtime), 100000000)))
        elif fnum == 4 and wtype == 2:
            new_fields.append((4, 2, val))
            found_f4 = True
        elif fnum == 15 and wtype == 2:
            found_f15 = True
            sub = parse_pb_fields(val) if val else []
            other_sub = [(sfn, swt, sv) for sfn, swt, sv in sub if sfn not in (1, 4, 5, 7)]
            new_sub = [(1, 2, title_bytes)]
            if archived:
                new_sub.append((4, 0, 1))
                new_sub.append((5, 2, encode_timestamp(asec, ananos)))
            new_sub.append((7, 2, encode_timestamp(vsec, vnanos)))
            new_sub.extend(other_sub)
            new_fields.append((15, 2, encode_pb_fields(new_sub)))
        else:
            new_fields.append((fnum, wtype, val))

    if not found_f1:
        new_fields.insert(0, (1, 2, title_bytes))
    if not found_f4 and cid:
        new_fields.append((4, 2, cid.encode("utf-8")))
    if not found_f15:
        new_sub = [(1, 2, title_bytes)]
        if archived:
            new_sub.append((4, 0, 1))
            new_sub.append((5, 2, encode_timestamp(asec, ananos)))
        new_sub.append((7, 2, encode_timestamp(vsec, vnanos)))
        new_fields.append((15, 2, encode_pb_fields(new_sub)))

    return encode_pb_fields(new_fields)


def update_hub_proto_bytes(pb_data, updates_dict, now_sec):
    top_fields = parse_pb_fields(pb_data) if pb_data else []
    new_top = []
    seen_cids = set()
    for fnum, wtype, val in top_fields:
        if fnum == 1 and wtype == 2:
            entry = parse_pb_fields(val)
            cid = next((v.decode("utf-8", errors="ignore") for fn, wt, v in entry if fn == 1), "")
            raw = next((v for fn, wt, v in entry if fn == 2 and wt == 2), b"")
            seen_cids.add(cid)
            if cid in updates_dict:
                tgt_t, tgt_arch, prev_annot, db_raw, ov_mt = updates_dict[cid]
                base_raw = db_raw if db_raw and len(db_raw) >= len(raw) else raw
                new_raw = update_summary_pb(base_raw, cid, tgt_t, tgt_arch, prev_annot, now_sec, override_mtime=ov_mt)
                new_entry = [(1, 2, cid.encode("utf-8")), (2, 2, new_raw)]
                new_top.append((1, 2, encode_pb_fields(new_entry)))
            else:
                new_top.append((fnum, wtype, val))
        else:
            new_top.append((fnum, wtype, val))
    for cid, (tgt_t, tgt_arch, prev_annot, db_raw, ov_mt) in updates_dict.items():
        if cid not in seen_cids and db_raw:
            new_raw = update_summary_pb(db_raw, cid, tgt_t, tgt_arch, prev_annot, now_sec, override_mtime=ov_mt)
            new_entry = [(1, 2, cid.encode("utf-8")), (2, 2, new_raw)]
            new_top.append((1, 2, encode_pb_fields(new_entry)))
    return encode_pb_fields(new_top)


def load_proto_summaries_map():
    res = {}
    if not os.path.exists(PROTO_PATH):
        return res
    try:
        pb_data = open(PROTO_PATH, "rb").read()
        for fnum, wtype, val in parse_pb_fields(pb_data):
            if fnum == 1 and wtype == 2:
                entry = parse_pb_fields(val)
                cid = next((v.decode("utf-8", errors="ignore") for fn, wt, v in entry if fn == 1), "")
                raw = next((v for fn, wt, v in entry if fn == 2 and wt == 2), b"")
                if cid and raw:
                    res[cid] = parse_summary_pb_state(raw)
    except Exception:
        pass
    return res


# ---------------- Rules & Parsing ----------------

def load_rules():
    rules = {"merge": {}, "keep": [], "hide": [], "active_days": 3.0}
    if os.path.exists(RULES_PATH):
        try:
            rules.update(json.load(open(RULES_PATH, encoding="utf-8")))
        except Exception:
            pass
    return rules


def save_rules(rules):
    json.dump(rules, open(RULES_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def group_name(title, rules):
    t = (title or "").strip()
    cleaned = re.sub(r"^\s*续写\s*[:：·]\s*", "", t).strip()
    merge_map = rules.get("merge", {})
    if cleaned in merge_map:
        return merge_map[cleaned]
    if t in merge_map:
        return merge_map[t]
    for piece, name in merge_map.items():
        if piece and (piece in cleaned or piece in t):
            return name
    return cleaned or "(无标题)"


def parse_annot_pbtxt(cid):
    p = os.path.join(ANNOT_DIR, f"{cid}.pbtxt")
    res = {"title": None, "archived": False, "arch_sec": None, "arch_nanos": None, "view_sec": None, "view_nanos": None}
    if not os.path.exists(p):
        return res
    txt = open(p, encoding="utf-8", errors="ignore").read()
    m_title = re.search(r'title:"((?:[^"\\]|\\.)*)"', txt)
    if m_title:
        res["title"] = m_title.group(1).replace('\\"', '"').replace("\\\\", "\\")
    if re.search(r"\barchived:true\b", txt):
        res["archived"] = True
    m_arch = re.search(r"archival_status_timestamp:\{seconds:(\d+)(?:\s+nanos:(\d+))?\}", txt)
    if m_arch:
        res["arch_sec"] = int(m_arch.group(1))
        res["arch_nanos"] = int(m_arch.group(2) or 0)
    m_view = re.search(r"last_user_view_time:\{seconds:(\d+)(?:\s+nanos:(\d+))?\}", txt)
    if m_view:
        res["view_sec"] = int(m_view.group(1))
        res["view_nanos"] = int(m_view.group(2) or 0)
    return res


def write_annot_pbtxt(cid, title, archived, prev_annot, now_sec, override_mtime=None):
    os.makedirs(ANNOT_DIR, exist_ok=True)
    esc_title = title.replace("\\", "\\\\").replace('"', '\\"')
    parts = [f'title:"{esc_title}"']
    if archived:
        asec = prev_annot.get("arch_sec") or int(now_sec)
        ananos = prev_annot.get("arch_nanos") or 100000000
        parts.append("archived:true")
        parts.append(f"archival_status_timestamp:{{seconds:{asec}  nanos:{ananos}}}")
    vsec = int(override_mtime) if override_mtime else (prev_annot.get("view_sec") or int(now_sec))
    vnanos = prev_annot.get("view_nanos") or 100000000
    parts.append(f"last_user_view_time:{{seconds:{vsec}  nanos:{vnanos}}}")
    p = os.path.join(ANNOT_DIR, f"{cid}.pbtxt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("  ".join(parts))


def parse_iso_ts(s):
    if not s:
        return 0.0
    try:
        s_clean = s[:19].replace(" ", "T")
        dt = datetime.datetime.strptime(s_clean, "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return 0.0


def format_iso_utc(ts):
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.100000+00:00")


def transcript_path(cid):
    return os.path.join(BRAIN_DIR, cid, ".system_generated", "logs", "transcript.jsonl")


def context_k(cid):
    p = transcript_path(cid)
    if not os.path.exists(p):
        return None
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        f.seek(max(0, size - 4 * 1024 * 1024))
        lines = f.read().split(b"\n")
    for line in reversed(lines):
        if b'"PLANNER_RESPONSE"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "PLANNER_RESPONSE":
            continue
        tot = (d.get("input_tokens") or 0) + (d.get("cache_read_tokens") or 0)
        if tot > 0:
            return tot // 1000
    return None


def cost_tag(k):
    if k is None:
        return "?"
    if k <= CHEAP_K:
        return "直接续"
    if k <= PRICEY_K:
        return "可续·偏贵"
    return "先交接"


def SafeFilename(name):
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip()


def ws_to_cwd(ws_json):
    try:
        uris = json.loads(ws_json or "[]")
        if uris:
            u = uris[0]
            if u.startswith("file://"):
                return u[7:]
    except Exception:
        pass
    return os.path.expanduser("~")


# ---------------- 1:1 Mirror Seed Builder (Claude -> Antigravity) ----------------

def build_mirror_conversation(g, cid, tid, mtime, handoff_text, is_nb_xiaoke=False):
    """为来自 Claude 的线程在反重力中构建完整的 2 步原生 SQLite 会话 (.db) + transcript.jsonl + raw_summary。"""
    tpl_db = os.path.join(CONV_DIR, f"{TPL_ANS_CID}.db")
    if not os.path.exists(tpl_db):
        return None, "[]"

    c_tpl = sqlite3.connect(tpl_db)
    s0_meta, s0_pay = c_tpl.execute("SELECT metadata, step_payload FROM steps WHERE idx=0").fetchone()
    s1_meta, s1_pay = c_tpl.execute("SELECT metadata, step_payload FROM steps WHERE idx=1").fetchone()
    em_data = c_tpl.execute("SELECT data FROM executor_metadata WHERE idx=0").fetchone()[0]
    tmb_data = c_tpl.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()[0]
    c_tpl.close()

    cs_conn = sqlite3.connect(DB_PATH, timeout=5.0)
    raw_ans = cs_conn.execute("SELECT raw_summary FROM conversation_summaries WHERE conversation_id=?", (TPL_ANS_CID,)).fetchone()[0]
    raw_eeo = None
    if is_nb_xiaoke:
        r_eeo = cs_conn.execute("SELECT raw_summary FROM conversation_summaries WHERE conversation_id=?", (TPL_EEO_CID,)).fetchone()
        if r_eeo and r_eeo[0]:
            raw_eeo = r_eeo[0]
    cs_conn.close()

    def repl_ids(blob):
        return blob.replace(TPL_ANS_CID.encode(), cid.encode()).replace(TPL_ANS_TID.encode(), tid.encode())

    new_s0_meta = repl_ids(s0_meta)
    new_s1_meta = repl_ids(s1_meta)
    new_em = repl_ids(em_data)
    new_tmb = repl_ids(tmb_data)
    if is_nb_xiaoke and os.path.exists(os.path.join(CONV_DIR, f"{TPL_EEO_CID}.db")):
        try:
            c_eeo = sqlite3.connect(os.path.join(CONV_DIR, f"{TPL_EEO_CID}.db"))
            eeo_tmb = c_eeo.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main'").fetchone()[0]
            c_eeo.close()
            new_tmb = eeo_tmb.replace(TPL_EEO_CID.encode(), cid.encode())
        except Exception:
            pass

    user_prompt = f"【双端共研同步】请加载 Claude Desktop 端「{g}」的最新交接摘要并准备在反重力端共同开发。"
    u_bytes = user_prompt.encode("utf-8")
    f19_raw = dict((fn, v) for fn, wt, v in parse_pb_fields(s0_pay) if fn == 19)[19]
    f19_sub = [(sfn, swt, sv) for sfn, swt, sv in parse_pb_fields(f19_raw) if sfn not in (2, 3, 9)]
    new_f19 = encode_pb_fields([(2, 2, u_bytes), (3, 2, encode_pb_fields([(1, 2, u_bytes)]))] + f19_sub)
    new_s0_pay = encode_pb_fields([(1, 0, 14), (4, 0, 3), (5, 2, new_s0_meta), (19, 2, new_f19)])

    a_bytes = (handoff_text or f"# 双端共研交接单：{g}\n已在双端建立 1:1 镜像，可直接在此续写。").encode("utf-8")
    bot_id = f"bot-{uuid.uuid4()}".encode("utf-8")
    f20_raw = dict((fn, v) for fn, wt, v in parse_pb_fields(s1_pay) if fn == 20)[20]
    f20_sub = [(sfn, swt, sv) for sfn, swt, sv in parse_pb_fields(f20_raw) if sfn not in (1, 3, 6, 8, 14)]
    new_f20 = encode_pb_fields([(1, 2, a_bytes), (6, 2, bot_id), (8, 2, a_bytes)] + f20_sub)
    new_s1_pay = encode_pb_fields([(1, 0, 15), (4, 0, 3), (5, 2, new_s1_meta), (20, 2, new_f20)])

    os.makedirs(CONV_DIR, exist_ok=True)
    target_db = os.path.join(CONV_DIR, f"{cid}.db")
    if os.path.exists(target_db):
        os.remove(target_db)
    conn = sqlite3.connect(target_db)
    conn.execute("PRAGMA user_version = 1;")
    conn.execute("CREATE TABLE `trajectory_meta` (`trajectory_id` text,`cascade_id` text,`trajectory_type` integer,`source` integer,PRIMARY KEY (`trajectory_id`));")
    conn.execute("CREATE TABLE `steps` (`idx` integer,`step_type` integer NOT NULL DEFAULT 0,`status` integer NOT NULL DEFAULT 0,`has_subtrajectory` numeric NOT NULL DEFAULT false,`metadata` blob,`error_details` blob,`permissions` blob,`task_details` blob,`render_info` blob,`step_payload` blob,`step_format` integer NOT NULL DEFAULT 0,PRIMARY KEY (`idx`));")
    conn.execute("CREATE TABLE `gen_metadata` (`idx` integer,`data` blob,`size` integer NOT NULL DEFAULT 0,PRIMARY KEY (`idx`));")
    conn.execute("CREATE TABLE `executor_metadata` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));")
    conn.execute("CREATE TABLE `parent_references` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));")
    conn.execute("CREATE TABLE `trajectory_metadata_blob` (`id` text DEFAULT \"main\",`data` blob,PRIMARY KEY (`id`));")
    conn.execute("CREATE TABLE `battle_mode_infos` (`idx` integer,`data` blob,PRIMARY KEY (`idx`));")

    conn.execute("INSERT INTO trajectory_meta VALUES (?, ?, 4, 1)", (tid, cid))
    conn.execute(
        "INSERT INTO steps VALUES (0, 14, 3, 0, ?, NULL, NULL, NULL, NULL, ?, 0)",
        (sqlite3.Binary(new_s0_meta), sqlite3.Binary(new_s0_pay)),
    )
    conn.execute(
        "INSERT INTO steps VALUES (1, 15, 3, 0, ?, NULL, NULL, NULL, NULL, ?, 0)",
        (sqlite3.Binary(new_s1_meta), sqlite3.Binary(new_s1_pay)),
    )
    conn.execute("INSERT INTO executor_metadata VALUES (0, ?)", (sqlite3.Binary(new_em),))
    conn.execute("INSERT INTO trajectory_metadata_blob VALUES ('main', ?)", (sqlite3.Binary(new_tmb),))
    conn.commit()
    conn.close()
    os.chmod(target_db, 0o600)

    # 写入 transcript.jsonl 供快速检索与 token 统计
    t_path = transcript_path(cid)
    os.makedirs(os.path.dirname(t_path), exist_ok=True)
    iso_str = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    est_tokens = max(1000, len(a_bytes) // 2)
    lines = [
        json.dumps({"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": iso_str, "content": user_prompt}, ensure_ascii=False),
        json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": iso_str, "content": a_bytes.decode("utf-8", errors="ignore"), "input_tokens": est_tokens, "output_tokens": 500}, ensure_ascii=False),
    ]
    with open(t_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.utime(t_path, (mtime, mtime))

    # 构建 raw_summary
    base_raw = repl_ids(raw_ans)
    if is_nb_xiaoke and raw_eeo:
        eeo_f9_f17 = [(fn, wt, v.replace(TPL_EEO_CID.encode(), cid.encode())) for fn, wt, v in parse_pb_fields(raw_eeo) if fn in (9, 17)]
        ans_other = [(fn, wt, v) for fn, wt, v in parse_pb_fields(base_raw) if fn not in (9, 17)]
        base_raw = encode_pb_fields(ans_other + eeo_f9_f17)
        ws_uris = '["file://" + os.path.expanduser("~")]'
    else:
        ws_uris = '["file://" + os.path.expanduser("~")]'

    annot = {"view_sec": int(mtime), "view_nanos": 100000000}
    final_raw = update_summary_pb(base_raw, cid, g, False, annot, int(mtime), override_mtime=int(mtime))
    return final_raw, ws_uris


# ---------------- Scanning & Sync ----------------

def scan_conversations(rules):
    proto_map = load_proto_summaries_map()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conversation_id, title, step_count, last_modified_time, status, workspace_uris, raw_summary
            FROM conversation_summaries
            WHERE parent_conversation_id = '' OR parent_conversation_id IS NULL
            ORDER BY last_modified_time DESC;
            """
        )
        rows = cur.fetchall()
        conn.close()
    except Exception:
        conn = sqlite3.connect(f"file:{DB_PATH}?immutable=1", uri=True)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conversation_id, title, step_count, last_modified_time, status, workspace_uris, raw_summary
            FROM conversation_summaries
            WHERE parent_conversation_id = '' OR parent_conversation_id IS NULL
            ORDER BY last_modified_time DESC;
            """
        )
        rows = cur.fetchall()
        conn.close()

    groups = {}
    for cid, db_title, steps, lmt, status, ws, raw_sum in rows:
        annot = parse_annot_pbtxt(cid)
        pb_title, pb_arch = proto_map.get(cid, (None, None))
        raw_title, raw_arch = parse_summary_pb_state(raw_sum)

        live_title = pb_title if pb_title is not None else (db_title or "")
        live_archived = bool(pb_arch) if pb_arch is not None else bool(raw_arch)

        mt = parse_iso_ts(lmt)
        t_p = transcript_path(cid)
        if os.path.exists(t_p):
            mt = max(mt, os.path.getmtime(t_p))

        # 如果新创建的会话还没有自动生成标题（或此前因无标题被误标为 (已隐藏)），从首条用户消息提取标题，绝不误杀新线程！
        eff_title = live_title
        if not eff_title or eff_title in ("(无标题)", "(已隐藏)"):
            if steps == 0:
                g = "__hide__"
                eff_title = "__hide__"
            elif os.path.exists(t_p):
                first_u = ""
                try:
                    with open(t_p, encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if '"USER_INPUT"' in line:
                                d = json.loads(line)
                                if d.get("type") == "USER_INPUT":
                                    u = clean_user_msg(d.get("content") or "")
                                    if u:
                                        first_u = " ".join(u.split())
                                        break
                except Exception:
                    pass
                if first_u:
                    eff_title = first_u[:28]
                else:
                    eff_title = "__hide__" 

        if eff_title == "__hide__" or any(h and (h == eff_title or h in eff_title or (annot["title"] and annot["title"] not in ("(无标题)", "(已隐藏)") and (h == annot["title"] or h in annot["title"]))) for h in rules.get("hide", [])):
            g = "__hide__"
        else:
            g = group_name(eff_title, rules)
            if g == eff_title and annot["title"] and annot["title"] not in ("(无标题)", "(已隐藏)"):
                g_annot = group_name(annot["title"], rules)
                if g_annot != annot["title"]:
                    g = g_annot
            if any(h and h == g for h in rules.get("hide", [])):
                g = "__hide__"

        is_mirror = (cid == mirror_cid_for(g)) and (steps <= 2)
        item = {
            "cid": cid,
            "db_title": db_title or "",
            "cur_title": live_title,
            "steps": steps,
            "mt": mt,
            "status": status or "",
            "ws": ws or "[]",
            "archived": live_archived,
            "annot": annot,
            "raw_sum": raw_sum or b"",
            "is_mirror": is_mirror,
            "in_sync": (
                pb_title is not None
                and live_title == (annot["title"] or "")
                and (db_title or "") == live_title
                and live_archived == annot["archived"]
                and bool(pb_arch) == bool(raw_arch)
            ),
        }
        groups.setdefault(g, []).append(item)
    return groups


def load_claude_manifest():
    if os.path.exists(CLAUDE_MANIFEST_PATH):
        try:
            return json.load(open(CLAUDE_MANIFEST_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_agy_manifest(report):
    os.makedirs(HANDOFFS_DIR, exist_ok=True)
    manifest = {}
    for g, latest, mt, k, active, _, _, is_mirror in report:
        if not active or is_mirror:
            continue
        manifest[g] = {
            "cid": latest["cid"],
            "mtime": mt,
            "k": k,
            "cwd": ws_to_cwd(latest["ws"]),
            "steps": latest["steps"],
        }
    with open(AGY_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def sync(apply=False, quiet=False):
    rules = load_rules()
    groups = scan_conversations(rules)
    claude_manifest = load_claude_manifest()
    cutoff = time.time() - float(rules.get("active_days", 3.0)) * 86400
    plan = []
    report = []

    for g, members in groups.items():
        # 优先选择非镜像原生线程，其次按最新修改时间排序
        members.sort(key=lambda x: (not x["is_mirror"], x["mt"]), reverse=True)
        if g == "__hide__":
            for m in members:
                if not m["archived"] or not m["in_sync"]:
                    plan.append(("archive", m, m["cur_title"] or "(已隐藏)", True, "hide规则"))
            continue

        latest = members[0]
        mt = latest["mt"]
        if latest["is_mirror"] and g in claude_manifest:
            mt = claude_manifest[g].get("mtime", mt)
        is_running = any("RUNNING" in m["status"] for m in members)
        keep_rule = any(k and (k in g or k == g) for k in rules.get("keep", []))
        active = (mt >= cutoff) or keep_rule or is_running
        k = context_k(latest["cid"])
        report.append((g, latest, mt, k, active, keep_rule, len(members), latest["is_mirror"]))

        for idx, m in enumerate(members):
            if idx == 0 and active:
                need_rename = m["cur_title"] != g or m["db_title"] != g or not m["in_sync"]
                need_unarchive = m["archived"]
                # 如果它是镜像种子且 Claude 端有更新的交接单，则刷新镜像内容
                safe_g = SafeFilename(g)
                h_file = os.path.join(CLAUDE_HANDOFFS_DIR, f"{safe_g}.auto.md")
                db_file = os.path.join(CONV_DIR, f"{m['cid']}.db")
                need_mirror_refresh = (
                    m["is_mirror"]
                    and os.path.exists(h_file)
                    and (not os.path.exists(db_file) or os.path.getmtime(db_file) < os.path.getmtime(h_file))
                )
                if need_mirror_refresh:
                    plan.append(("mirror_seed", m, g, False, "刷新Claude镜像交接单"))
                elif need_rename or need_unarchive:
                    act = "show" if need_unarchive else "rename"
                    plan.append((act, m, g, False, "主线程规范命名/恢复"))
            else:
                if not m["archived"] or not m["in_sync"]:
                    reason = "同组旧重复线程" if idx > 0 else f"超过{rules['active_days']}天未活动"
                    target_t = g if g != "(无标题)" else (m["cur_title"] or "(无标题)")
                    plan.append(("archive", m, target_t, True, reason))

    # 检查 Claude 端有、而反重力端还没有的线程 -> 1:1 镜像注入
    for cg, cinfo in claude_manifest.items():
        if cg in groups or any(h and (h == cg or h in cg) for h in rules.get("hide", [])):
            continue
        mcid = mirror_cid_for(cg)
        mt = float(cinfo.get("mtime") or time.time())
        ws_str = '["file://" + os.path.expanduser("~")]' if cg.startswith("NB小科") else '["file://" + os.path.expanduser("~")]'
        fake_m = {
            "cid": mcid,
            "db_title": "",
            "cur_title": "",
            "steps": 2,
            "mt": mt,
            "status": "CASCADE_RUN_STATUS_IDLE",
            "ws": ws_str,
            "archived": False,
            "annot": {"view_sec": int(mt), "view_nanos": 100000000},
            "raw_sum": b"",
            "is_mirror": True,
            "in_sync": False,
        }
        plan.append(("mirror_seed", fake_m, cg, False, "从Claude 1:1镜像注入"))
        report.append((cg, fake_m, mt, 2, True, True, 1, True))

    save_agy_manifest(report)

    if not quiet:
        tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
        if os.path.exists(tok_script):
            try:
                r = subprocess.run([sys.executable, tok_script, "--banner"], capture_output=True, text=True, timeout=15)
                if r.stdout.strip():
                    print(r.stdout.strip() + "\n")
            except Exception:
                pass
        report.sort(key=lambda r: -r[2])
        print(f"{'双端统一主线程 (领域 · 主题)':<28} {'双端状态':<12} {'更新时间':<11} {'步数':>5} {'上下文':>6}  {'建议':<8} {'ID前缀'}")
        print("-" * 92)
        for g, latest, mt, k, active, keep_rule, cnt, is_mirror in report:
            if not active:
                continue
            ks = f"{k}k" if k is not None else "?"
            if is_mirror:
                role = "Claude镜像"
            elif g in claude_manifest:
                role = "⚡双端共研"
            else:
                role = "AGY主控"
            tag = f"{g}{f' [{cnt}合1]' if cnt > 1 else ''}"
            t_str = time.strftime("%m-%d %H:%M", time.localtime(mt))
            print(f"{tag[:27]:<28} {role:<12} {t_str:<11} {latest['steps']:>5} {ks:>6}  {cost_tag(k):<8} {latest['cid'][:8]}")

        counts = {}
        for act, m, tgt_t, tgt_arch, reason in plan:
            counts[act] = counts.get(act, 0) + 1
        names = {"rename": "改名对齐", "show": "恢复并改名", "archive": "归档隐藏", "mirror_seed": "跨端1:1镜像"}
        summary_str = "，".join(f"{names[k]} {v} 个" for k, v in sorted(counts.items())) or "无需改动"
        print(f"\n计划改动：{summary_str}")
        if plan and not apply:
            for act, m, tgt_t, tgt_arch, reason in plan[:25]:
                print(f"  - [{names[act]}] {m['cid'][:8]} {m['cur_title']!r} -> {tgt_t!r} ({reason})")
            if len(plan) > 25:
                print(f"  ... 及另外 {len(plan) - 25} 项")
            print("\n（当前只看不改。确认后运行：agy-sessions --apply）")
            return report
        if not plan:
            return report

    if not plan:
        return report

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUPS_DIR, f"sessions-{stamp}")
    os.makedirs(bdir, exist_ok=True)
    if os.path.exists(ANNOT_DIR):
        shutil.copytree(ANNOT_DIR, os.path.join(bdir, "annotations"), dirs_exist_ok=True)
    if os.path.exists(PROTO_PATH):
        shutil.copy2(PROTO_PATH, os.path.join(bdir, "agyhub_summaries_proto.pb"))

    now_sec = int(time.time())
    updates_dict = {}
    new_db_rows = []

    for act, m, tgt_t, tgt_arch, _ in plan:
        cid = m["cid"]
        if act == "mirror_seed":
            mt = m["mt"]
            tid = mirror_tid_for(tgt_t)
            safe_g = SafeFilename(tgt_t)
            h_path = os.path.join(CLAUDE_HANDOFFS_DIR, f"{safe_g}.auto.md")
            h_text = open(h_path, encoding="utf-8", errors="ignore").read() if os.path.exists(h_path) else ""
            raw_sum, ws_uris = build_mirror_conversation(tgt_t, cid, tid, mt, h_text, is_nb_xiaoke=tgt_t.startswith("NB小科"))
            if raw_sum:
                write_annot_pbtxt(cid, tgt_t, False, m["annot"], now_sec, override_mtime=int(mt))
                updates_dict[cid] = (tgt_t, False, m["annot"], raw_sum, int(mt))
                new_db_rows.append((cid, tgt_t, 2, format_iso_utc(mt), "CASCADE_RUN_STATUS_IDLE", ws_uris, raw_sum))
                h_file = os.path.join(CLAUDE_HANDOFFS_DIR, f"{safe_g}.auto.md")
                db_file = os.path.join(CONV_DIR, f"{cid}.db")
                if os.path.exists(db_file) and os.path.exists(h_file):
                    hm = os.path.getmtime(h_file)
                    os.utime(db_file, (hm, hm))
        else:
            write_annot_pbtxt(cid, tgt_t, tgt_arch, m["annot"], now_sec)
            updates_dict[cid] = (tgt_t, tgt_arch, m["annot"], m.get("raw_sum") or b"", None)

    # 1. 更新 agyhub_summaries_proto.pb 并加 UF_IMMUTABLE (uchg) 锁
    if os.path.exists(PROTO_PATH):
        try:
            os.chflags(PROTO_PATH, 0)
            os.chmod(PROTO_PATH, 0o600)
        except Exception:
            pass
        pb_data = open(PROTO_PATH, "rb").read()
        new_pb_data = update_hub_proto_bytes(pb_data, updates_dict, now_sec)
        with open(PROTO_PATH, "wb") as f:
            f.write(new_pb_data)
        try:
            if "--lock" in sys.argv: os.chflags(PROTO_PATH, stat.UF_IMMUTABLE)
        except Exception:
            pass

    # 2. 同步更新 conversation_summaries.db 并插入新增的跨端镜像行
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cur = conn.cursor()
        for cid, tgt_t, sc, lmt, st, ws_u, raw_s in new_db_rows:
            proj_id = "19dabf4d-7410-4101-9631-c86c15af547e" if tgt_t.startswith("NB小科") else "ab61184a-ff37-4eb3-8007-2b47e41bac65"
            cur.execute("SELECT 1 FROM conversation_summaries WHERE conversation_id = ?", (cid,))
            if cur.fetchone():
                cur.execute(
                    "UPDATE conversation_summaries SET title=?, preview=?, step_count=?, last_modified_time=?, last_user_input_time=?, status=?, workspace_uris=?, project_id=?, raw_summary=? WHERE conversation_id=?",
                    (tgt_t, tgt_t, sc, lmt, lmt, st, ws_u, proj_id, sqlite3.Binary(raw_s), cid),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO conversation_summaries
                    (conversation_id, title, preview, step_count, last_modified_time, workspace_uris, status, project_id, parent_conversation_id, last_user_input_time, last_user_input_step_index, app_data_dir, raw_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, 0, 'antigravity', ?)
                    """,
                    (cid, tgt_t, tgt_t, sc, lmt, ws_u, st, proj_id, lmt, sqlite3.Binary(raw_s)),
                )
        for cid, (tgt_t, tgt_arch, prev_annot, db_raw, ov_mt) in updates_dict.items():
            if any(r[0] == cid for r in new_db_rows):
                continue
            cur.execute("SELECT raw_summary FROM conversation_summaries WHERE conversation_id = ?", (cid,))
            row = cur.fetchone()
            base_raw = (row[0] if row and row[0] else db_raw) or b""
            new_raw = update_summary_pb(base_raw, cid, tgt_t, tgt_arch, prev_annot, now_sec, override_mtime=ov_mt)
            cur.execute(
                "UPDATE conversation_summaries SET title = ?, raw_summary = ? WHERE conversation_id = ?",
                (tgt_t, sqlite3.Binary(new_raw), cid),
            )
        conn.commit()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception:
            pass
        conn.close()
    except Exception as e:
        if not quiet:
            print(f"警告：写入 SQLite 失败：{e}")

    if not quiet:
        print(f"已同步更新 {len(plan)} 项会话（含跨端 1:1 镜像注入，备份在 {bdir}），并已锁定 agyhub_summaries_proto.pb！")
        print("Cmd+Q 重启一次 Antigravity 即可在侧边栏看到全部 38 个统一主线程。")
    return report


# ---------------- Unified Dual-App Handoff Generator ----------------

def clean_user_msg(raw):
    txt = (raw or "").split("<ADDITIONAL_METADATA>")[0]
    return txt.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()


def sanitize_section_body(body):
    if not body:
        return ""
    body = re.sub(r"<!-- (?:CLAUDE|AGY)_SECTION_(?:START|END) -->\n?", "", body)
    body = re.sub(r"<!-- (?:MTIME|K): \d+ -->\n?", "", body)
    body = re.sub(r"^# 双端共研交接单：.*$", "### 历史交接单引用", body, flags=re.MULTILINE)
    return body.strip()


def extract_section_block(text, side):
    pattern = rf"<!-- {side}_SECTION_START -->\s*<!-- MTIME: (\d+) -->\s*<!-- K: (\d+) -->\n(.*?)\n<!-- {side}_SECTION_END -->"
    m = re.search(pattern, text or "", flags=re.DOTALL)
    if not m:
        return None
    return {"mtime": int(m.group(1)), "k": int(m.group(2)), "body": sanitize_section_body(m.group(3))}


def write_merged_handoff(g, side, section_body, mtime, k):
    safe_g = SafeFilename(g)
    os.makedirs(HANDOFFS_DIR, exist_ok=True)
    os.makedirs(CLAUDE_HANDOFFS_DIR, exist_ok=True)
    c_dst = os.path.join(CLAUDE_HANDOFFS_DIR, f"{safe_g}.auto.md")
    a_dst = os.path.join(HANDOFFS_DIR, f"{safe_g}.auto.md")

    existing = ""
    if os.path.exists(c_dst):
        existing = open(c_dst, encoding="utf-8", errors="ignore").read()
    elif os.path.exists(a_dst):
        existing = open(a_dst, encoding="utf-8", errors="ignore").read()

    c_blk = extract_section_block(existing, "CLAUDE")
    a_blk = extract_section_block(existing, "AGY")
    cur_blk = {"mtime": int(mtime or time.time()), "k": int(k or 0), "body": sanitize_section_body(section_body)}
    if side == "CLAUDE":
        c_blk = cur_blk
    else:
        a_blk = cur_blk

    blocks = []
    if c_blk:
        blocks.append(("CLAUDE", "Claude Desktop", c_blk))
    if a_blk:
        blocks.append(("AGY", "反重力 Antigravity", a_blk))
    blocks.sort(key=lambda x: x[2]["mtime"], reverse=True)

    now_str = time.strftime("%Y-%m-%d %H:%M")
    header = [
        f"# 双端共研交接单：{g}",
        f"- **最后自动刷新**：{now_str}（零 token 提取，Claude × 反重力双端共用）",
    ]
    if len(blocks) == 2:
        b0, b1 = blocks[0], blocks[1]
        t0 = time.strftime("%m-%d %H:%M", time.localtime(b0[2]["mtime"]))
        t1 = time.strftime("%m-%d %H:%M", time.localtime(b1[2]["mtime"]))
        header.append(f"- **⚡ 最新活跃端**：**{b0[1]}**（{t0}，上下文 {b0[2]['k']}k） | **协同端**：**{b1[1]}**（{t1}，上下文 {b1[2]['k']}k）")
    elif len(blocks) == 1:
        b0 = blocks[0]
        t0 = time.strftime("%m-%d %H:%M", time.localtime(b0[2]["mtime"]))
        header.append(f"- **⚡ 当前主控端**：**{b0[1]}**（{t0}，上下文 {b0[2]['k']}k） | **另一端**：已 1:1 镜像同步待命")
    header.append(f"- **跨端接手口令**：在任意一端直接发 `读 ~/.claude/session-handoffs/{safe_g}.auto.md 接着做`")
    header.append("")

    manual = os.path.join(CLAUDE_HANDOFFS_DIR, f"{safe_g}.md")
    if os.path.exists(manual):
        header += [
            f"## 📌 手写交接补充（{time.strftime('%m-%d %H:%M', time.localtime(os.path.getmtime(manual)))}）",
            f"见 `{manual}`，优先读它。",
            "",
        ]

    body_parts = []
    for s_code, _, blk in blocks:
        body_parts.append(
            f"<!-- {s_code}_SECTION_START -->\n"
            f"<!-- MTIME: {blk['mtime']} -->\n"
            f"<!-- K: {blk['k']} -->\n"
            f"{blk['body']}\n"
            f"<!-- {s_code}_SECTION_END -->"
        )

    full_text = "\n".join(header) + "\n\n".join(body_parts) + "\n"
    with open(c_dst, "w", encoding="utf-8") as f:
        f.write(full_text)
    with open(a_dst, "w", encoding="utf-8") as f:
        f.write(full_text)
    return c_dst, full_text


DIRECTION_KEYWORDS = (
    "不对", "不要", "严禁", "必须", "改用", "改成", "别", "方向", "重点", "注意",
    "只用", "禁止", "错了", "有问题", "为什么", "红线", "铁律", "不能", "优先", "先做",
    "停", "重来", "不是这个", "记住", "以后", "核心", "目标", "要求"
)


def extract_git_ground_truth(cwd):
    if not cwd or not os.path.isdir(cwd):
        return []
    out = []
    try:
        br = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, capture_output=True, text=True, timeout=3)
        branch = (br.stdout or "").strip()
        if not branch:
            return []
        lg = subprocess.run(["git", "log", "--oneline", "-5"], cwd=cwd, capture_output=True, text=True, timeout=3)
        commits = [x.strip() for x in (lg.stdout or "").splitlines() if x.strip()]
        st = subprocess.run(["git", "status", "-s"], cwd=cwd, capture_output=True, text=True, timeout=3)
        dirty = [x.rstrip() for x in (st.stdout or "").splitlines() if x.strip()]
        out.append("### 🧬 Git 与工作区实时物理真源（源自 `davila7/session-handoff`）")
        out.append(f"- **当前分支**：`{branch}` | **未提交改动数**：`{len(dirty)}` 个文件")
        if commits:
            out.append("- **最近 5 次 Git Commit（新→旧）**：")
            for c in commits[:5]:
                out.append(f"  - `{c}`")
        if dirty:
            out.append("- **当前工作区未提交/修改中文件（Top 12）**：")
            for d_line in dirty[:12]:
                out.append(f"  - `{d_line}`")
        out.append("")
    except Exception:
        pass
    return out


def write_brief(g, member, quiet=False):
    if member.get("is_mirror"):
        return None
    cid = member["cid"]
    t_p = transcript_path(cid)
    if not os.path.exists(t_p):
        if not quiet:
            print(f"找不到该线程的 transcript.jsonl：{t_p}")
        return None

    users, agent_replies = [], []
    files_written = {}
    bash_cmds = []
    with open(t_p, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            ts = (d.get("created_at") or "")[5:16].replace("T", " ")
            if t == "USER_INPUT":
                u = clean_user_msg(d.get("content") or "")
                if u and not u.startswith(("【双端共研同步", "【跨号防封安全交接")):
                    users.append((ts, u))
            elif t == "PLANNER_RESPONSE":
                c = (d.get("content") or "").strip()
                if c and not c.startswith("# 双端共研交接单：") and "<!-- CLAUDE_SECTION_START -->" not in c and "<!-- AGY_SECTION_START -->" not in c:
                    agent_replies.append((ts, c))
                for tc in d.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    tname = tc.get("name") or tc.get("tool_name") or ""
                    args = tc.get("arguments") or tc.get("args") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    if not isinstance(args, dict):
                        continue
                    fpath = args.get("Path") or args.get("file_path") or args.get("path") or ""
                    if ("write_file" in tname or "edit_file" in tname) and fpath:
                        info = files_written.setdefault(fpath, {"op": "Edit(增量修改)", "count": 0, "last_ts": ts})
                        if "write_file" in tname:
                            info["op"] = "Write(新建/覆写)"
                        info["count"] += 1
                        info["last_ts"] = ts
                    elif "run_command" in tname:
                        cmd_s = (args.get("CommandLine") or "").strip()
                        if cmd_s and not cmd_s.startswith(("ls ", "pwd", "echo ")):
                            bash_cmds.append((ts, " ".join(cmd_s.split())[:220]))

    b_dir = os.path.join(BRAIN_DIR, cid)
    artifacts = []
    if os.path.exists(b_dir):
        for fn in sorted(os.listdir(b_dir)):
            if fn.endswith(".md"):
                artifacts.append(os.path.join(b_dir, fn))

    k = context_k(cid) or 0
    mt_str = time.strftime("%m-%d %H:%M", time.localtime(member["mt"]))
    cwd = ws_to_cwd(member["ws"])
    quality_score = 80 + (10 if files_written or bash_cmds else 0) + (10 if agent_replies else 0)
    sec = [
        f"## 🔵 反重力 (Antigravity) 端进展（更新时间：{mt_str} | 步数：{member['steps']} | 原上下文：{k}k | 交接质量分：{quality_score}/100 [READY]）",
        f"- **原记录**：`{t_p}`（ID: `{cid}`，共 `{len(users)}` 轮用户指令，修改 `{len(files_written)}` 个文件）",
        f"- **工作区**：`{member['ws']}`",
        f"- **按需查细节**：`python3 ~/.gemini/antigravity/scripts/agy-digest.py {cid[:8]} 25`（严禁整份读 transcript.jsonl）",
        "",
    ]
    if artifacts:
        sec.append("### 📄 反重力沉淀的 Artifact 文档")
        for a in artifacts:
            sec.append(f"- `{a}`")
        sec.append("")

    if len(users) > 10:
        sec += ["### 🎯 1. 原始立项北极星（The Goal — 会话首轮立项初心，严防跑偏）"]
        for ts, u in users[:2]:
            sec.append(f"- **{ts}（首轮锚点）**：{' '.join(u.split())[:600]}")
        sec.append("")

    guardrails = []
    seen_g = set()
    for ts, u in users[:-3]:
        if any(kw in u for kw in DIRECTION_KEYWORDS):
            clean_u = " ".join(u.split())[:500]
            if clean_u[:80] not in seen_g:
                seen_g.add(clean_u[:80])
                guardrails.append((ts, clean_u))
    if guardrails:
        sec += ["### 🧭 2. 用户核心方向纠偏与红线偏好（User Feedback & Guardrails — 必读防跑偏）"]
        for ts, gu in guardrails[-8:]:
            sec.append(f"- **{ts}**：{gu}")
        sec.append("")

    sec += extract_git_ground_truth(cwd)

    if files_written:
        sec += ["### 🛠️ 3. 会话内真实修改/新建文件账本（Files Changed Ledger）"]
        sorted_files = sorted(files_written.items(), key=lambda kv: kv[1]["last_ts"], reverse=True)
        for fpath, info in sorted_files[:20]:
            sec.append(f"- `{fpath}` — **{info['op']}**（共 {info['count']} 次，最后更新 {info['last_ts']}）")
        sec.append("")

    if bash_cmds:
        sec += ["### ⚡ 4. 最近关键执行与测试命令（Key Commands Executed）"]
        seen_c = set()
        dedup_cmds = []
        for ts_c, cmd_s in reversed(bash_cmds):
            if cmd_s not in seen_c:
                seen_c.add(cmd_s)
                dedup_cmds.append((ts_c, cmd_s))
            if len(dedup_cmds) >= 6:
                break
        for ts_c, cmd_s in reversed(dedup_cmds):
            sec.append(f"- {ts_c}：`{cmd_s}`")
        sec.append("")

    sec.append("### 📌 5. 反重力端 · 用户最近 12 条指令（旧→新，最后 3 条完整保留）")
    recent_users = users[-12:]
    for idx, (ts, u) in enumerate(recent_users):
        if idx >= len(recent_users) - 3:
            sec.append(f"- **{ts}（最新关键指令）**：\n  {u[:1500]}")
        else:
            one_line = " ".join(u.split())[:450]
            sec.append(f"- {ts}：{one_line}")

    sec += ["", "### 🚀 6. 反重力端 · Agent 最后 6 段关键交付与下一步结论（旧→新）"]
    for ts, r in agent_replies[-6:]:
        sec += [f"#### {ts}", r[:1800], ""]

    dst, full_text = write_merged_handoff(g, "AGY", "\n".join(sec), member["mt"], k)
    if not quiet:
        est_k = len(full_text) // 2 // 1000 + 1
        print(f"已刷新双端合并交接单：{dst}（约 {est_k}k token，反重力原线程 {k}k，质量分 {quality_score}/100）")
    return dst


def brief(piece):
    rules = load_rules()
    groups = scan_conversations(rules)
    hits = []
    for g, members in groups.items():
        if g == "__hide__":
            continue
        if piece == g or piece in g or any(piece in m["cid"] or piece in m["cur_title"] for m in members):
            hits.append(g)
    if len(hits) != 1:
        print("没找到唯一匹配的线程，候选：" + ("、".join(hits) if hits else "无"))
        return
    g = hits[0]
    members = sorted(groups[g], key=lambda x: (not x["is_mirror"], x["mt"]), reverse=True)
    write_brief(g, members[0], quiet=False)


def brief_all(quiet=False):
    rules = load_rules()
    groups = scan_conversations(rules)
    cnt = 0
    for g, members in groups.items():
        if g == "__hide__":
            continue
        members.sort(key=lambda x: (not x["is_mirror"], x["mt"]), reverse=True)
        m = members[0]
        if not m["is_mirror"] and not m["archived"]:
            write_brief(g, m, quiet=True)
            cnt += 1
    if not quiet:
        print(f"已批量刷新 {cnt} 个反重力原生主线程的双端交接单。")


def write_cross_tool_index(report):
    os.makedirs(HANDOFFS_DIR, exist_ok=True)
    os.makedirs(CLAUDE_HANDOFFS_DIR, exist_ok=True)
    claude_manifest = load_claude_manifest()
    tok_banner = ""
    tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
    if os.path.exists(tok_script):
        try:
            r = subprocess.run([sys.executable, tok_script, "--banner"], capture_output=True, text=True, timeout=15)
            tok_banner = r.stdout.strip()
        except Exception:
            pass
    lines = [
        "# Claude × 反重力 (Antigravity) 双端 1:1 统一线程与交接总表",
        f"最后自动刷新：{time.strftime('%Y-%m-%d %H:%M')}（双端侧边栏 1:1 镜像对齐 · 38 条统一「领域 · 主题」主线程）",
        "",
    ]
    if tok_banner:
        lines += [
            "## 💰 实时 Token 额度与今日排班导航（详表见 `TOKEN_BUDGET.md`）",
            "```text",
            tok_banner,
            "```",
            "",
        ]
    lines += [
        "| 统一主线程名称 (领域 · 主题) | 协同模式 | 最近更新时间 | 续写建议 | 双端共用零 Token 交接单 |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    for g, latest, mt, k, active, _, _, is_mirror in sorted(report, key=lambda r: r[0]):
        if not active:
            continue
        if is_mirror:
            mode = "🟣 Claude主 → 已镜像AGY"
            ck = claude_manifest.get(g, {}).get("k")
            tag = cost_tag(ck) + f" (Claude {ck}k)" if ck else "直接续"
        elif g in claude_manifest:
            mode = "⚡ 双端原生共研"
            ck = claude_manifest.get(g, {}).get("k")
            tag = f"AGY {k or '?'}k / Claude {ck or '?'}k"
        else:
            mode = "🔵 AGY主 → 已镜像Claude"
            tag = f"{cost_tag(k)} (AGY {k or '?'}k)"
        t_str = time.strftime("%m-%d %H:%M", time.localtime(mt))
        safe_g = SafeFilename(g)
        lines.append(f"| `{g}` | {mode} | {t_str} | {tag} | `~/.claude/session-handoffs/{safe_g}.auto.md` |")

    text = "\n".join(lines) + "\n"
    with open(os.path.join(HANDOFFS_DIR, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(CLAUDE_HANDOFFS_DIR, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write(text)


def edit_list(which, piece):
    rules = load_rules()
    other = "hide" if which == "keep" else "keep"
    rules[other] = [x for x in rules.get(other, []) if x != piece]
    if piece not in rules.setdefault(which, []):
        rules[which].append(piece)
    save_rules(rules)
    print(f"已记录规则：{'常驻保留' if which == 'keep' else '归档隐藏'}「{piece}」。跑 agy-sessions 查看，agy-sessions --apply 生效。")


def add_rename(src_piece, target_name):
    rules = load_rules()
    rules.setdefault("merge", {})[src_piece] = target_name
    save_rules(rules)
    print(f"已记录改名/合并规则：包含「{src_piece}」->「{target_name}」。跑 agy-sessions --apply 生效。")


def auto():
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    brief_all(quiet=True)
    report = sync(apply=True, quiet=True)
    write_cross_tool_index(report)
    for d in glob.glob(os.path.join(BACKUPS_DIR, "sessions-*")):
        if os.path.getmtime(d) < time.time() - 3 * 86400:
            shutil.rmtree(d, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if args[:1] == ["brief"] and len(args) > 1:
        brief(" ".join(args[1:]))
    elif args[:1] == ["brief-all"]:
        brief_all(quiet=False)
    elif args[:1] in (["keep"], ["hide"]) and len(args) > 1:
        edit_list(args[0], " ".join(args[1:]))
    elif args[:1] == ["rename"] and len(args) >= 3:
        add_rename(args[1], " ".join(args[2:]))
    elif args[:1] in (["tokens"], ["token"], ["budget"]):
        tok_script = os.path.expanduser("~/.claude/scripts/tokens.py")
        subprocess.run([sys.executable, tok_script] + args[1:])
    elif args[:1] == ["auto"]:
        auto()
    elif args[:1] in (["-h"], ["--help"], ["help"]):
        print(__doc__.strip())
    else:
        if "--apply" in args:
            brief_all(quiet=True)
        report = sync(apply=("--apply" in args))
        if "--apply" in args:
            write_cross_tool_index(report)


if __name__ == "__main__":
    main()
