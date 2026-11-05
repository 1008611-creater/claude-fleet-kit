#!/usr/bin/env python3
"""打印反重力 (Antigravity) 会话记录 transcript.jsonl 里的用户/助手文字（跳过工具调用与系统噪音）。
用法:
  python3 ~/.gemini/antigravity/scripts/agy-digest.py <transcript.jsonl或conversation_id> [最后N条, 默认20] [--users 只看用户消息]
"""
import json, os, sys

BRAIN = os.path.expanduser("~/.gemini/antigravity/brain")


def resolve_path(arg):
    if os.path.exists(arg):
        if os.path.isdir(arg):
            return os.path.join(arg, ".system_generated", "logs", "transcript.jsonl")
        return arg
    cand = os.path.join(BRAIN, arg, ".system_generated", "logs", "transcript.jsonl")
    if os.path.exists(cand):
        return cand
    # prefix match on conversation UUID
    for d in os.listdir(BRAIN):
        if d.startswith(arg):
            p = os.path.join(BRAIN, d, ".system_generated", "logs", "transcript.jsonl")
            if os.path.exists(p):
                return p
    return arg


def clean_user_text(raw):
    txt = raw.split("<ADDITIONAL_METADATA>")[0]
    txt = txt.replace("<USER_REQUEST>", "").replace("</USER_REQUEST>", "").strip()
    return txt


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)
    path = resolve_path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 20
    users_only = "--users" in sys.argv
    if not os.path.exists(path):
        print(f"找不到记录文件: {path}")
        sys.exit(1)

    out = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = d.get("type")
            ts = (d.get("created_at") or "")[:16].replace("T", " ")
            if t == "USER_INPUT":
                txt = clean_user_text(d.get("content") or "")
                if txt:
                    out.append((f"USER {ts}", txt[:3000]))
            elif t == "PLANNER_RESPONSE" and not users_only:
                txt = (d.get("content") or "").strip()
                if txt:
                    out.append((f"AGENT {ts}", txt[:3000]))

    for who, x in out[-n:]:
        print(f"===== {who}\n{x}\n")


if __name__ == "__main__":
    main()
