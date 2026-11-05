#!/usr/bin/env python3
"""打印会话记录 jsonl 里的用户/助手文字（跳过工具输出）。用法: jsonl-digest.py <file> [最后N条, 默认80] [--users 只看用户消息]"""
import json, sys
f = sys.argv[1]; n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 80
users_only = '--users' in sys.argv
out = []
for line in open(f, encoding='utf-8', errors='ignore'):
    try: d = json.loads(line)
    except Exception: continue
    t = d.get('type')
    if t == 'summary': out.append(('SUMMARY', d.get('summary', ''))); continue
    if t not in ('user', 'assistant'): continue
    c = (d.get('message') or {}).get('content')
    texts = [c] if isinstance(c, str) else [b.get('text', '') for b in (c or []) if isinstance(b, dict) and b.get('type') == 'text']
    for x in texts:
        x = x.strip()
        if not x or x.startswith('<system-reminder>') or x.startswith('<local-command'): continue
        if users_only and t != 'user': continue
        out.append((('USER' if t == 'user' else 'CLAUDE') + ' ' + d.get('timestamp', '')[:16], x[:3000]))
for who, x in out[-n:]:
    print(f'===== {who}\n{x}\n')
