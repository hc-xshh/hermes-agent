#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '.')

# 读取 API key - 找第一个非注释的 OPENROUTER_API_KEY 行
key = None
with open(os.path.expanduser('~/.hermes/.env')) as f:
    for line in f:
        s = line.strip()
        if s.startswith('#'):
            continue
        if 'OPENROUTER_API_KEY' in s and '=' in s:
            key = s.split('=', 1)[1]
            break

if key:
    os.environ['OPENROUTER_API_KEY'] = key
    print(f'Key loaded: len={len(key)}')
else:
    print('No key found!')

from tools.vision_tools import check_vision_requirements
result = check_vision_requirements()
print(f'check_vision_requirements() = {result}')
