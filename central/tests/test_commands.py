import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from agent import commands
from models import AuditLog, get_session
from routers import servers
from websocket_manager import AgentConnection, WebSocketManager


class CommandAPITests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.identity = {'username': 'admin', 'role': 'admin'}
        self.manager = WebSocketManager()
        self.conn = AgentConnection(Mock(), 'srv-one', {'capabilities': {'host_commands': True, 'command_protocol': 1}})
        self.manager._agents['srv-one'] = self.conn
        self.manager.request_from_agent = AsyncMock(return_value={'exit_code': 0, 'stdout': 'host-one', 'stderr': '', 'timed_out': False})
        app = FastAPI()
        app.include_router(servers.router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[servers.get_current_user_info] = lambda: self.identity
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        patcher = patch.object(servers, 'manager', self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, **kwargs):
        return self.client.post('/api/servers/srv-one/command', json={'command': 'hostname', **kwargs})

    def test_command_preserves_text_timeout_and_audits_without_secrets(self):
        cmd = 'printf "%s" "secret-token"\nhostname'
        response = self.post(command=cmd, timeout=90)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['stdout'], 'host-one')
        self.manager.request_from_agent.assert_awaited_once_with('srv-one', action='host_command', params={'command': cmd, 'timeout': 90}, timeout=100)
        events = self.session.exec(select(AuditLog)).all()
        self.assertEqual(len(events), 2)
        self.assertNotIn('secret-token', str([e.detail for e in events]))

    def test_access_is_checked_before_dispatch(self):
        self.identity['role'] = 'user'
        self.assertEqual(self.post().status_code, 403)
        self.identity['role'] = 'admin'
        with patch.object(servers, 'get_allowed_agent_ids', return_value=set()):
            self.assertEqual(self.post().status_code, 403)
        self.manager.request_from_agent.assert_not_called()

    def test_offline_old_and_disabled_agents_do_not_execute(self):
        self.conn.ws = None
        self.assertEqual(self.post().status_code, 503)
        self.conn.ws = Mock()
        self.conn.info = {}
        self.assertEqual(self.post().status_code, 409)
        self.conn.info = {'capabilities': {'host_commands': False, 'command_protocol': 1}}
        self.assertEqual(self.post().status_code, 409)
        self.manager.request_from_agent.assert_not_called()

    def test_invalid_commands_limits_and_concurrency(self):
        for cmd in ['', 'x' * 16001, '  ', 'abc\x00']:
            self.assertIn(self.post(command=cmd).status_code, [400, 422])
        for timeout in [0, 121, True, 1.2, '30']:
            self.assertEqual(self.post(timeout=timeout).status_code, 422)
        with patch.object(servers, '_active_commands', 16):
            self.assertEqual(self.post().status_code, 429)
        self.manager.request_from_agent.assert_not_called()

    def test_agent_error_does_not_retry_and_releases_slot(self):
        self.manager.request_from_agent.side_effect = RuntimeError('Agent disconnected')
        self.assertEqual(self.post().status_code, 503)
        self.manager.request_from_agent.assert_awaited_once()
        self.assertEqual(servers._active_commands, 0)

    def test_nonzero_exit_is_returned_as_command_result(self):
        self.manager.request_from_agent.return_value = {'exit_code': 2, 'stderr': 'syntax error', 'timed_out': False}
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['exit_code'], 2)


class CommandRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The production agent runs on Linux; Windows test hosts lack SIGKILL.
        patcher = patch.object(commands.signal, 'SIGKILL', 9, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, stdout=b'', stderr=b''):
        proc = Mock(pid=123456, returncode=0)
        proc.stdout = asyncio.StreamReader()
        proc.stdout.feed_data(stdout)
        proc.stdout.feed_eof()
        proc.stderr = asyncio.StreamReader()
        proc.stderr.feed_data(stderr)
        proc.stderr.feed_eof()
        proc.wait = AsyncMock(return_value=0)
        return proc

    async def test_opt_in_validation_and_agent_concurrency(self):
        with patch.object(commands.asyncio, 'create_subprocess_exec', new=AsyncMock()) as spawn:
            with self.assertRaises(ValueError):
                await commands.run_command('hostname', 30)
            with patch.object(commands, '_running', 4):
                with self.assertRaises(ValueError):
                    await commands.run_command('hostname', 30, host_access_enabled=True)
            for cmd, timeout in [(' ', 30), ('x\x00', 30), ('x', True), ('x', 121)]:
                with self.assertRaises(ValueError):
                    await commands.run_command(cmd, timeout, host_access_enabled=True)
            spawn.assert_not_called()

    async def test_shell_argv_output_and_truncation(self):
        proc = self.process(b'a' * (commands.OUTPUT_LIMIT + 20000), 'błąd'.encode())
        with patch.object(commands.asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=proc)) as spawn, patch.object(commands.os, 'killpg', create=True):
            result = await commands.run_command('echo "$HOSTNAME"; exit 0', 30, host_access_enabled=True)
        self.assertEqual(spawn.call_args.args[-3:], ('/bin/sh', '-c', 'echo "$HOSTNAME"; exit 0'))
        self.assertIn('--root', spawn.call_args.args)
        self.assertIn('--wd=/proc/1/root', spawn.call_args.args)
        self.assertNotIn('AGENT_TOKEN', spawn.call_args.kwargs['env'])
        self.assertEqual(len(result['stdout']), commands.OUTPUT_LIMIT)
        self.assertEqual(result['stderr'], 'błąd')
        self.assertTrue(result['truncated'])
        self.assertEqual(commands._running, 0)

    async def test_timeout_kills_process_group_and_releases_slot(self):
        proc = self.process(b'partial output')
        async def wait():
            if proc.returncode is None:
                await asyncio.sleep(10)
            return proc.returncode
        proc.returncode = None
        proc.wait = wait
        def killed(*args):
            proc.returncode = -9
        with patch.object(commands.asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=proc)), patch.object(commands.os, 'killpg', side_effect=killed, create=True) as kill:
            result = await commands.run_command('sleep 10', 1, host_access_enabled=True)
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['stdout'], 'partial output')
        self.assertEqual(result['exit_code'], -9)
        kill.assert_called()
        self.assertEqual(commands._running, 0)

    async def test_real_subprocess_capture_and_timeout(self):
        # Exercise real OS pipes and process cleanup; replace only the Linux
        # namespace launcher so this test also runs on Windows development hosts.
        original_spawn = asyncio.create_subprocess_exec
        spawned = []

        async def spawn(*args, **kwargs):
            proc = await original_spawn(*args, **kwargs)
            spawned.append(proc)
            return proc

        def kill(*args):
            proc = spawned[-1]
            if proc.returncode is None:
                proc.kill()

        with patch.object(commands, 'HOST_SHELL', (sys.executable,)), patch.object(commands.asyncio, 'create_subprocess_exec', new=spawn), patch.object(commands.os, 'killpg', side_effect=kill, create=True):
            result = await commands.run_command('import sys; print("real output"); print("real error", file=sys.stderr); sys.exit(7)', 10, host_access_enabled=True)
            self.assertEqual(result['exit_code'], 7)
            self.assertIn('real output', result['stdout'])
            self.assertIn('real error', result['stderr'])
            result = await commands.run_command('import time; print("before timeout", flush=True); time.sleep(20)', 1, host_access_enabled=True)
            self.assertTrue(result['timed_out'])
            self.assertIn('before timeout', result['stdout'])
            self.assertIsNotNone(spawned[-1].returncode)
            self.assertEqual(commands._running, 0)


class RequestTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_cancellation_remove_pending_request(self):
        manager = WebSocketManager()
        conn = AgentConnection(Mock(send_text=AsyncMock()), 'srv', {})
        manager._agents['srv'] = conn
        with self.assertRaises(RuntimeError):
            await manager.request_from_agent('srv', 'host_command', timeout=0.01)
        self.assertFalse(conn.pending_requests)
        task = asyncio.create_task(manager.request_from_agent('srv', 'host_command'))
        await asyncio.sleep(0)
        self.assertTrue(conn.pending_requests)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(conn.pending_requests)


if __name__ == '__main__':
    unittest.main()
