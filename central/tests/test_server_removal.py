import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from websocket_manager import AgentConnection, DashboardSession, WebSocketManager


class RemovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_remove_offline_and_reconnect(self):
        manager = WebSocketManager()
        manager._agents['old'] = AgentConnection(None, 'old', {})
        await manager.remove_offline_agent('old')
        self.assertIsNone(manager.get_agent('old'))
        await manager.register_agent(AsyncMock(), {'agent_name': 'old'})
        self.assertTrue(manager.is_agent_online('old'))

    async def test_connected_even_if_stale_cannot_be_removed(self):
        manager = WebSocketManager()
        conn = AgentConnection(AsyncMock(), 'old', {})
        manager._agents['old'] = conn
        for last_seen in (conn.last_seen, 0):
            conn.last_seen = last_seen
            with self.assertRaises(ValueError):
                await manager.remove_offline_agent('old')
            self.assertIs(manager._agents['old'], conn)

    async def test_missing_agent(self):
        with self.assertRaises(KeyError):
            await WebSocketManager().remove_offline_agent('missing')

    async def test_removal_event_respects_access(self):
        manager = WebSocketManager()
        for name, allowed in [('admin', None), ('member', {'old'}), ('other', {'new'})]:
            manager._dashboards[name] = DashboardSession(AsyncMock(), name, 'user', allowed)
        await manager.broadcast_to_dashboards('agent_removed', {'agent_id': 'old'})
        manager._dashboards['admin'].ws.send_text.assert_awaited_once()
        manager._dashboards['member'].ws.send_text.assert_awaited_once()
        manager._dashboards['other'].ws.send_text.assert_not_awaited()


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Isolate auth and persistence; exercise the real route and role checks.
        auth = types.ModuleType('auth')
        auth.get_current_user_info = lambda: {'username': 'admin', 'role': 'admin'}
        models = types.ModuleType('models')
        models.get_session = lambda: None
        models.get_allowed_agent_ids = lambda *args: None
        models.log_audit = lambda *args, **kwargs: None
        spec = importlib.util.spec_from_file_location(
            'servers_under_test', Path(__file__).resolve().parents[1] / 'routers' / 'servers.py')
        cls.routes = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'auth': auth, 'models': models}):
            spec.loader.exec_module(cls.routes)

    def setUp(self):
        self.manager = WebSocketManager()
        self.manager._agents['old'] = AgentConnection(None, 'old', {})
        self.app = FastAPI()
        self.app.include_router(self.routes.router)
        self.app.dependency_overrides[self.routes.get_session] = lambda: None
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        for name, value in [('manager', self.manager), ('log_audit', None), ('get_allowed_agent_ids', None)]:
            patcher = patch.object(self.routes, name, value) if name == 'manager' else patch.object(self.routes, name, return_value=value)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            if name == 'log_audit':
                self.audit = mock

    def test_delete_and_audit(self):
        response = self.client.delete('/api/servers/old')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        self.assertIsNone(self.manager.get_agent('old'))
        self.audit.assert_called_once()
        self.assertEqual(json.loads(self.audit.call_args.kwargs['detail']), {'agent_id': 'old'})
        self.assertEqual(self.client.delete('/api/servers/old').status_code, 404)

    def test_user_forbidden(self):
        self.app.dependency_overrides[self.routes.get_current_user_info] = lambda: {'username': 'user', 'role': 'user'}
        self.assertEqual(self.client.delete('/api/servers/old').status_code, 403)
        self.assertIsNotNone(self.manager.get_agent('old'))
        self.audit.assert_not_called()

    def test_online_conflict(self):
        self.manager._agents['old'].ws = AsyncMock()
        self.assertEqual(self.client.delete('/api/servers/old').status_code, 409)
        self.assertTrue(self.manager.is_agent_online('old'))
        self.audit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
