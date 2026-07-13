#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, '.')

# 读取 API key
with open(os.path.expanduser('~/.hermes/.env')) as f:
    for line in f:
        line = line.strip()
        if not line.startswith('#') and 'OPENROUTER_API_KEY=' in line:
            key = line.split('=', 1)[1]
            os.environ['OPENROUTER_API_KEY'] = key
            print(f'Key length: {len(key)}')
            break

from tools.vision_tools import check_vision_requirements
result = check_vision_requirements()
print(f'check_vision_requirements() = {result}')
