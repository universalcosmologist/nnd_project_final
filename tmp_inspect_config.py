import os
import tempfile
from unittest.mock import patch
import agent

with patch.dict(os.environ, {'AGENT_API_KEY': 'shared-secret'}, clear=False):
    with tempfile.NamedTemporaryFile('w', delete=False) as handle:
        handle.write('{}')
        temp_path = handle.name
    print('patched env', os.environ.get('AGENT_API_KEY'))
    cfg = agent.load_config(temp_path, env=os.environ)
    print(cfg)
    os.remove(temp_path)
