"""Run an isolated browser-test central on localhost:18082, with a temporary DB.

Install central/requirements.txt first. Test credentials match test_fleet_integration.cjs.
Stop with Ctrl+C. This utility never reads or writes the deployment database.
"""
import os
import sys
import tempfile
from pathlib import Path


if __name__ == '__main__':
    central = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='dockermind-fleet-') as temporary:
        os.environ.update({
            'CT_USERNAME': 'admin', 'CT_PASSWORD': 'Fleet-Local-2026!',
            'CT_SECRET_KEY': 'fleet-test-only-secret-key-32-characters-long',
            'AGENT_SECRET_TOKEN': 'fleet-preview-agent-token',
            'AI_API_TOKEN': 'test-only', 'DB_PATH': str(Path(temporary) / 'preview.db'),
        })
        sys.path.insert(0, str(central))
        os.chdir(central)
        import uvicorn
        from main import app
        from models import engine
        try:
            uvicorn.run(app, host='127.0.0.1', port=18082, log_level='warning')
        finally:
            engine.dispose()
