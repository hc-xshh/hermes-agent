#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '.')

# 读取 API key
with open(os.path.expanduser('~/.hermes/.env')) as f:
    for line in f:
        stripped = line.strip()
        if not stripped.startswith('#') and stripped.startswith('OPENROUTER_API_KEY='):
            key = stripped.split('=', 1)[1]
            os.environ['OPENROUTER_API_KEY'] = key
            print(f'Key length: {len(key)}')
            break

from agent.image_routing import _supports_vision_override
from hermes_cli.config import load_config
cfg = load_config()

# 检查 override
override = _supports_vision_override(cfg, 'openrouter', 'xiaomi/mimo-v2.5-pro')
print(f'_supports_vision_override = {override}')

# 检查 config 中的 supports_vision
model_cfg = cfg.get('model', {})
sv = model_cfg.get('supports_vision', {})
print(f'model.supports_vision = {sv}')
print(f'type(model_cfg) = {type(model_cfg)}')
print(f'model keys = {list(model_cfg.keys()) if isinstance(model_cfg, dict) else "N/A"}')
