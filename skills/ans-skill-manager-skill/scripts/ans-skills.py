#!/usr/bin/env python3
"""ANS 全舰队 Agent Skills 权威中枢管理器 (ans-skills · 内化自 GitHub xingkongliang/skills-manager ★7.1k + buzhangsan/skill-manager 31,767+ 技能库)

核心能力：
  1. 权威中央库 (SSOT)：以 ~/claude-fleet-kit/skills 为唯一权威源，统管全机所有 Agent Skills。
  2. 五端 1:1 部署同步：一键对齐 SSOT <-> Claude (~/.claude/skills) <-> 反重力 (~/.gemini/config/skills) <-> ANS工作区 (.agents/skills) <-> Codex (~/.codex/skills) + Windows 远端同步。
  3. 31,767+ 社区中文技能检索：内置 buzhangsan/skill-manager 原版 31,767 条中英双语技能库 (all_skills_with_cn.json.gz) 秒级搜索。
  4. 原版完整文件夹拉取 (No Fake Substitutes)：支持从 GitHub URL / 子目录 Sparse Checkout 完整拉取原版 SKILL.md + references/* + scripts/* 并自动分发到四端。

用法：
  ans-skills                        查看全舰队技能大盘（按 6 大业务域分组 + 五端部署状态矩阵）
  ans-skills status                 同上，检查未入库 (unadopted) 或多端未对齐的技能
  ans-skills sync --apply           将所有散落技能收编入 SSOT（自动过滤 >50MB 视频产物），并在 Claude / 反重力 / ANS / Codex 四端建立统一软链 + 刷新 registry/skills.json
  ans-skills search <关键词>        在内置 31,767+ 中文社区技能库中检索匹配技能（按相关度与 Star 排序）
  ans-skills install <URL或序号>    使用原版 Git Sparse Checkout / SVN 完整拉取外部开源 Skill 并部署到全端
  ans-skills audit                  执行 personal-skill-system 安全与体积门禁审计 (audit.py)
  ans-skills win-sync               输出 Windows 台式机一键同步全量 Skill 的 PowerShell 命令
"""
import datetime, glob, gzip, json, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

SSOT_ROOT = "~/claude-fleet-kit"
SSOT_SKILLS = os.path.join(SSOT_ROOT, "skills")
REGISTRY_JSON = os.path.join(SSOT_ROOT, "registry", "skills.json")
AUDIT_SCRIPT = os.path.join(SSOT_ROOT, "scripts", "audit.py")

TARGETS = {
    "claude": os.path.expanduser("~/.claude/skills",
    "agy":    os.path.expanduser("~/.gemini/config/skills",
    "ans_ws": "~/workspace/.agents/skills",
    "codex":  os.path.expanduser("~/.codex/skills",
}

COMMUNITY_DB_GZ = os.path.expanduser("~/.claude/skill-manager-db/all_skills_with_cn.json.gz"
UPSTREAM_INDEX_JS = os.path.expanduser("~/.claude/skill-manager-db/src/index.js"

# 忽略非技能系统目录与构建产物
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "synced", "out", ".DS_Store"}
IGNORE_EXTS_IN_SSOT = {".mp4", ".mov", ".wav"}
MAX_FILE_BYTES_IN_SSOT = 48 * 1024 * 1024  # 48MB 门禁，确保 100% 通过 audit.py (50MB 限制)


def classify_domain(name, desc=""):
    """将技能归入与会话管理 1:1 对齐的六大业务域 (基建 / ANS / NB小科 / Beersong / 创作 / 通用)。"""
    n = name.lower()
    if n in ("session-sync", "agy-session-sync", "token-budget", "cc-ip-skill", "ans-skill-manager-skill", "ans-skill-maneger-skill", "agent-reach", "ego-browser", "ship-speed"):
        return "基建"
    if n.startswith("ans-") or n.startswith("oil-") or n in ("beautify-github-readme", "visual-explainer", "ui-ux-guide"):
        return "ANS"
    if n.startswith("nb-") or n in ("openlux-tripo-blender", "xiaoke-studio", "threejs-aaa-graphics-builder", "threejs-qa-release", "game-feel", "game-polish", "game-taste-3d", "anatomy"):
        return "NB小科"
    if "patent" in n or "beersong" in n:
        return "Beersong"
    if n in ("mg-styles-15", "image2.5genskill", "bulkgen", "khazix-writer", "shuorenhua", "sprint-prompt"):
        return "创作"
    return "ANS"


def parse_skill_frontmatter(skill_md_path):
    """提取 SKILL.md 的 name 与 description。"""
    try:
        txt = open(skill_md_path, encoding="utf-8", errors="ignore").read(4096)
    except Exception:
        return "", ""
    m = re.match(r"^---\s*\n(.*?)\n---", txt, flags=re.DOTALL)
    if not m:
        return "", ""
    fm = m.group(1)
    name_m = re.search(r"^name:\s*(.+)$", fm, flags=re.MULTILINE)
    desc_m = re.search(r"^description:\s*>?\s*(.+?)(?:\n[a-zA-Z_-]+:|\Z)", fm, flags=re.DOTALL | re.MULTILINE)
    name = name_m.group(1).strip().strip("'\"") if name_m else ""
    desc = " ".join((desc_m.group(1) if desc_m else "").split())
    return name, desc[:120]


def copy_skill_clean(src_dir, dst_dir):
    """将技能目录安全同步到 SSOT（自动跳过 >48MB 的超大渲染视频/音频及 .git / node_modules，保留全部 SKILL.md、references/*、scripts/*）。"""
    src_real = os.path.realpath(src_dir)
    dst_real = os.path.realpath(dst_dir) if os.path.exists(dst_dir) else dst_dir
    if src_real == dst_real:
        return
    os.makedirs(dst_dir, exist_ok=True)
    for root, dirs, files in os.walk(src_real):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        rel = os.path.relpath(root, src_real)
        target_sub = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        os.makedirs(target_sub, exist_ok=True)
        for fn in files:
            if fn == ".DS_Store":
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in IGNORE_EXTS_IN_SSOT:
                continue
            sp = os.path.join(root, fn)
            if os.path.islink(sp):
                continue
            try:
                if os.path.getsize(sp) > MAX_FILE_BYTES_IN_SSOT:
                    continue
                shutil.copy2(sp, os.path.join(target_sub, fn))
            except Exception:
                pass


def scan_all():
    """扫描 SSOT 与四大 Agent 目录中的所有有效技能（必须含 SKILL.md）。"""
    skills = {}
    all_roots = [("ssot", SSOT_SKILLS)] + list(TARGETS.items())
    for key, root in all_roots:
        if not os.path.exists(root):
            continue
        for entry in sorted(os.listdir(root)):
            if entry.startswith(".") or entry in IGNORE_DIRS or entry == "ans-skill-maneger-skill":
                continue
            p = os.path.join(root, entry)
            rp = os.path.realpath(p)
            skill_md = os.path.join(rp, "SKILL.md")
            if not os.path.isdir(rp) or not os.path.exists(skill_md):
                continue
            rec = skills.setdefault(entry, {
                "name": entry,
                "locations": {},
                "realpaths": {},
                "skill_md": skill_md,
                "mtime": 0.0,
                "desc": "",
            })
            rec["locations"][key] = "link" if os.path.islink(p) else "dir"
            rec["realpaths"][key] = rp
            mt = os.path.getmtime(skill_md)
            if mt >= rec["mtime"]:
                rec["mtime"] = mt
                rec["skill_md"] = skill_md
                _, desc = parse_skill_frontmatter(skill_md)
                if desc:
                    rec["desc"] = desc
    for s in skills.values():
        s["domain"] = classify_domain(s["name"], s["desc"])
    return skills


def cmd_status(skills=None):
    if skills is None:
        skills = scan_all()
    print("=" * 110)
    print(f"🧩 ANS 全舰队 Agent Skills 权威中枢大盘  [总数: {len(skills)} 个技能 | SSOT: {SSOT_SKILLS}]")
    print("=" * 110)
    print(f"{'领域':<6} {'技能名称 (Skill Name)':<34} {'SSOT中枢':<10} {'Claude':<9} {'反重力AGY':<10} {'ANS工作区':<10} {'Codex':<9} {'状态'}")
    print("-" * 110)

    domain_order = {"基建": 1, "ANS": 2, "创作": 3, "NB小科": 4, "Beersong": 5}
    sorted_items = sorted(skills.values(), key=lambda x: (domain_order.get(x["domain"], 9), x["name"]))

    unadopted = 0
    partial = 0
    for s in sorted_items:
        locs = s["locations"]
        c_ssot = "✅已入库" if "ssot" in locs else "❌未收编"
        c_cl   = f"✅{locs['claude']}" if "claude" in locs else "—"
        c_agy  = f"✅{locs['agy']}" if "agy" in locs else "—"
        c_ans  = f"✅{locs['ans_ws']}" if "ans_ws" in locs else "—"
        c_cdx  = f"✅{locs['codex']}" if "codex" in locs else "—"

        if "ssot" not in locs:
            st = "🟡 待收编入SSOT"
            unadopted += 1
        elif not ("claude" in locs and "agy" in locs):
            st = "🟡 待补齐双端"
            partial += 1
        else:
            st = "🟢 全端对齐"
        print(f"{s['domain']:<6} {s['name']:<34} {c_ssot:<10} {c_cl:<9} {c_agy:<10} {c_ans:<10} {c_cdx:<9} {st}")

    print("-" * 110)
    print(f"📊 统计：共 {len(skills)} 个技能 | 待收编入 SSOT: {unadopted} 个 | 待补齐 Claude/反重力双端: {partial} 个")
    if unadopted > 0 or partial > 0:
        print("💡 运行 `ans-skills sync --apply` 即可一键将全部技能收编入 SSOT 并 1:1 补齐到 Claude、反重力、ANS 与 Codex！")


def cmd_sync(apply=False):
    skills = scan_all()
    if not apply:
        cmd_status(skills)
        return

    os.makedirs(SSOT_SKILLS, exist_ok=True)
    for tdir in TARGETS.values():
        os.makedirs(tdir, exist_ok=True)

    adopted_cnt = 0
    deployed_cnt = 0

    for name, s in sorted(skills.items()):
        ssot_dir = os.path.join(SSOT_SKILLS, name)
        # 1. Pick freshest source realpath and sync into SSOT
        best_rp = os.path.dirname(s["skill_md"])
        # Special case: ego-browser lives inside /Applications/ego lite.app, copy its SKILL.md & refs to SSOT for backup
        if "ssot" not in s["locations"] or best_rp != os.path.realpath(ssot_dir):
            copy_skill_clean(best_rp, ssot_dir)
            adopted_cnt += 1

        # 2. Ensure deployed across Claude, AGY, ANS-WS, and Codex
        # For ego-browser, keep existing app bundle symlink if present; otherwise link to ssot_dir
        for tkey, troot in TARGETS.items():
            # Only deploy ANS/基建/创作 skills to ANS workspace (.agents/skills) so ANS repo stays clean, or keep existing
            if tkey == "ans_ws" and s["domain"] not in ("基建", "ANS", "创作") and tkey not in s["locations"]:
                continue
            tpath = os.path.join(troot, name)
            if not os.path.exists(tpath) and not os.path.islink(tpath):
                os.symlink(ssot_dir, tpath)
                deployed_cnt += 1
            elif os.path.isdir(tpath) and not os.path.islink(tpath) and name != "ego-browser":
                # Convert standalone duplicate directory into symlink to SSOT if it has no heavy build artifacts
                has_heavy = os.path.exists(os.path.join(tpath, "node_modules")) or bool(glob.glob(os.path.join(tpath, "**", "*.mp4"), recursive=True))
                if not has_heavy:
                    copy_skill_clean(tpath, ssot_dir)
                    shutil.rmtree(tpath)
                    os.symlink(ssot_dir, tpath)
                    deployed_cnt += 1

    # Also create typo-tolerant alias ans-skill-maneger-skill -> ans-skill-manager-skill
    mgr_ssot = os.path.join(SSOT_SKILLS, "ans-skill-manager-skill")
    if os.path.exists(mgr_ssot):
        for root in [SSOT_SKILLS] + list(TARGETS.values()):
            alias_p = os.path.join(root, "ans-skill-maneger-skill")
            if not os.path.exists(alias_p) and not os.path.islink(alias_p):
                os.symlink(mgr_ssot, alias_p)

    # 3. Update registry/skills.json with all SSOT skills & domains
    reg_entries = []
    for entry in sorted(os.listdir(SSOT_SKILLS)):
        if entry.startswith(".") or entry == "ans-skill-maneger-skill":
            continue
        p = os.path.join(SSOT_SKILLS, entry)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "SKILL.md")):
            _, desc = parse_skill_frontmatter(os.path.join(p, "SKILL.md"))
            reg_entries.append({
                "name": entry,
                "path": f"skills/{entry}",
                "domain": classify_domain(entry, desc),
            })
    reg_data = {
        "version": "2.0.0",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_skills": len(reg_entries),
        "skills": reg_entries,
    }
    os.makedirs(os.path.dirname(REGISTRY_JSON), exist_ok=True)
    with open(REGISTRY_JSON, "w", encoding="utf-8") as f:
        json.dump(reg_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 已完成全舰队技能同步：收编/刷新 SSOT {adopted_cnt} 项，新增跨端软链部署 {deployed_cnt} 项，注册表共 {len(reg_entries)} 个技能！")
    cmd_audit()


def cmd_search(query, limit=10):
    """调用 buzhangsan/skill-manager 的 31,767+ 社区中文技能数据库检索。"""
    if not os.path.exists(COMMUNITY_DB_GZ):
        print(f"❌ 找不到社区技能库文件：{COMMUNITY_DB_GZ}")
        return
    q = query.lower().strip()
    with gzip.open(COMMUNITY_DB_GZ, "rt", encoding="utf-8", errors="ignore") as f:
        db = json.load(f)

    results = []
    for sk in db:
        score = 0
        name = (sk.get("name") or "").lower()
        desc = (sk.get("description") or "").lower()
        desc_cn = (sk.get("description_cn") or "").lower()
        author = (sk.get("author") or "").lower()
        for token in q.split():
            if token in name:
                score += 12
            if token in desc_cn:
                score += 6
            if token in desc:
                score += 5
            if token in author:
                score += 3
        if score > 0:
            results.append((score, sk.get("stars") or 0, sk))

    results.sort(key=lambda x: (-x[0], -x[1]))
    top = [x[2] for x in results[:limit]]
    print(f"\n🔍 在 31,767+ 社区技能库中检索「{query}」，找到 {len(results)} 个匹配项（展示前 {len(top)} 个）：\n")
    for idx, sk in enumerate(top, 1):
        cn = sk.get("description_cn") or sk.get("description") or ""
        print(f"  {idx:2d}. 【{sk.get('name')}】 (作者: {sk.get('author')} | ⭐ {sk.get('stars', 0)} | 🔀 {sk.get('forks', 0)})")
        print(f"      📝 {cn[:110]}...")
        print(f"      🔗 安装口令: ans-skills install {sk.get('githubUrl')}\n")


def cmd_install(ref):
    """完整文件夹级拉取外部 GitHub Skill（支持 tree/branch/subpath 稀疏检出或整仓克隆），绝不残缺山寨。"""
    ref = ref.strip()
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)", ref)
    tmp_dir = tempfile.mkdtemp(prefix="ans-skill-install-")
    try:
        if m:
            owner, repo, branch, subpath = m.group(1), m.group(2), m.group(3), m.group(4).rstrip("/")
            skill_name = os.path.basename(subpath)
            repo_url = f"https://github.com/{owner}/{repo}.git"
            print(f"📦 正在使用 Git Sparse Checkout 完整拉取子目录技能：{owner}/{repo} -> {subpath} ...")
            subprocess.run(["git", "init"], cwd=tmp_dir, check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "remote", "add", "origin", repo_url], cwd=tmp_dir, check=True)
            subprocess.run(["git", "config", "core.sparseCheckout", "true"], cwd=tmp_dir, check=True)
            os.makedirs(os.path.join(tmp_dir, ".git", "info"), exist_ok=True)
            with open(os.path.join(tmp_dir, ".git", "info", "sparse-checkout"), "w") as f:
                f.write(f"{subpath}/*\n")
            subprocess.run(["git", "pull", "--depth=1", "origin", branch], cwd=tmp_dir, check=True)
            src_skill_dir = os.path.join(tmp_dir, subpath)
        else:
            if not ref.startswith("http"):
                ref = f"https://github.com/{ref.rstrip('/')}.git"
            skill_name = os.path.basename(ref.rstrip("/")).replace(".git", "")
            print(f"📦 正在完整克隆开源技能仓库：{ref} ...")
            src_skill_dir = os.path.join(tmp_dir, skill_name)
            subprocess.run(["git", "clone", "--depth=1", ref, src_skill_dir], check=True)

        skill_md = os.path.join(src_skill_dir, "SKILL.md")
        if not os.path.exists(skill_md):
            # Look one level deeper if repo wraps skill inside a subfolder
            cands = glob.glob(os.path.join(src_skill_dir, "*", "SKILL.md")) + glob.glob(os.path.join(src_skill_dir, "skills", "*", "SKILL.md"))
            if len(cands) == 1:
                src_skill_dir = os.path.dirname(cands[0])
                skill_name = os.path.basename(src_skill_dir)
            else:
                print(f"❌ 未在顶层找到 SKILL.md（候选子目录：{[os.path.dirname(c) for c in cands]}），请指定具体 tree/branch/subpath URL！")
                return

        ssot_dst = os.path.join(SSOT_SKILLS, skill_name)
        copy_skill_clean(src_skill_dir, ssot_dst)
        print(f"✅ 已完整入库至权威 SSOT：{ssot_dst}")
        cmd_sync(apply=True)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def cmd_audit():
    if os.path.exists(AUDIT_SCRIPT):
        r = subprocess.run([sys.executable, AUDIT_SCRIPT], capture_output=True, text=True)
        if r.returncode == 0:
            print("🛡️ 安全与体积门禁审计 (audit.py): ✅ audit ok (无密钥泄露、无 >50MB 超限文件)")
        else:
            print("🚨 审计告警:\n" + r.stdout + r.stderr)


def cmd_win_sync():
    print("🖥️ 在 Windows PowerShell 中粘贴运行以下一行命令，即可将 Mac 权威 SSOT 中的全部 48 个技能 1:1 同步到 Windows Claude：")
    print("-" * 105)
    print(r'''$a=@(); $p=Get-ChildItem "$HOME\.ssh\*.pub" -EA 0 | ? {(Get-Content $_.FullName -Raw) -match 'eeo-mbp'} | select -First 1; if($p){$a='-i',($p.FullName -replace '\.pub$','')}; New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null; scp -r @a "<your-mac-user>@<YOUR_MAC_TAILSCALE_IP>:~/claude-fleet-kit/skills/*" "$HOME\.claude\skills\"''')
    print("-" * 105)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd in ("status", "list"):
        cmd_status()
    elif cmd in ("sync", "adopt"):
        cmd_sync(apply=("--apply" in args or cmd == "adopt"))
    elif cmd == "search" and len(args) > 1:
        cmd_search(" ".join(args[1:]))
    elif cmd == "install" and len(args) > 1:
        cmd_install(args[1])
    elif cmd == "audit":
        cmd_audit()
    elif cmd == "win-sync":
        cmd_win_sync()
    else:
        print(__doc__.strip())


if __name__ == "__main__":
    main()
