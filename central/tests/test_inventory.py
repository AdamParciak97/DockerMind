import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from inventory import compose_metadata, evaluate_inventory, image_tag, matches
from models import AgentProfile, AlertEvent, InventoryRule, get_session
from routers import inventory as routes, servers
from websocket_manager import AgentConnection, WebSocketManager


def container(tag='9.2.6'):
    return {'name': 'elastic-1', 'image': 'registry:5000/elastic:9.2.6',
            'labels': {'com.docker.compose.service': 'elastic'},
            'compose': f'services:\n  elastic:\n    image: registry:5000/elastic:{tag}\n'}


class MetadataTests(unittest.TestCase):
    def test_tag_parsing(self):
        for ref, expected in [('registry:5000/elastic:9.3.1', '9.3.1'), ('registry:5000/elastic', 'latest'),
                              ('elastic@sha256:abc', None), ('elastic:9.3.1@sha256:abc', '9.3.1'),
                              ('elastic:${VERSION}', None), ('sha256:abc', None)]:
            self.assertEqual(image_tag(ref), expected)

    def test_service_and_yaml_anchor(self):
        c = container()
        c['compose'] = 'x-base: &base\n  image: elastic:9.3.1\nservices:\n  other:\n    image: other:1\n  elastic:\n    <<: *base\n'
        self.assertEqual(compose_metadata(c)['compose_tag'], '9.3.1')

    def test_unknown_compose(self):
        for content in [None, 'invalid: [', 'services: []', 'services: {elastic: {build: .}}',
                        '!!python/object:danger {}']:
            c = container()
            c['compose'] = content
            self.assertIsNone(compose_metadata(c)['compose_tag'])
        self.assertIsNone(compose_metadata(container('${VERSION}'))['compose_tag'])

    def test_case_insensitive_alternatives(self):
        rule = InventoryRule(name='test', field='name', patterns='ELK\nELASTIC', group_name='Elastic')
        self.assertTrue(matches(rule, container()))
        self.assertFalse(matches(rule, {'name': 'redis'}))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.rule = InventoryRule(name='Elastic', patterns='elastic', expected_tag='9.3.1', group_name='ELK')
        self.session.add(self.rule)
        self.session.commit()

    def evaluate(self, tag='9.2.6', agent='server'):
        c = container(tag)
        c.update(compose_metadata(c))
        return evaluate_inventory(self.session, agent, [c])

    def test_alert_dedup_ack_resolve_and_recur(self):
        alerts = self.evaluate()
        self.assertEqual(len(alerts), 1)
        self.assertIn('9.2.6', alerts[0]['message'])
        self.assertIn('9.3.1', alerts[0]['message'])
        self.assertEqual(self.evaluate(), [])
        event = self.session.exec(select(AlertEvent)).one()
        event.status = 'acknowledged'
        self.session.add(event)
        self.session.commit()
        self.assertEqual(self.evaluate(), [])
        self.assertEqual(self.evaluate('9.3.1')[0]['event'], 'alert_resolved')
        self.session.refresh(event)
        self.assertEqual(event.status, 'resolved')
        self.assertEqual(len(self.evaluate()), 1)

    def test_agents_are_independent_and_missing_data_alerts(self):
        self.assertEqual(len(self.evaluate(agent='one')), 1)
        self.assertEqual(len(self.evaluate(agent='two')), 1)
        self.assertEqual(len(self.evaluate('${VERSION}', agent='three')), 1)

    def test_disable_resolves_and_group_only_does_not_alert(self):
        self.evaluate()
        self.rule.enabled = False
        self.session.add(self.rule)
        self.session.commit()
        self.evaluate()
        self.assertEqual(self.session.exec(select(AlertEvent)).one().status, 'resolved')

    def test_api_rules_permissions_and_profile_persistence(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.include_router(servers.router)
        app.dependency_overrides[get_session] = lambda: self.session
        identity = {'username': 'admin', 'role': 'admin'}
        app.dependency_overrides[routes.get_current_user_info] = lambda: identity
        manager = WebSocketManager()
        manager._agents['server'] = AgentConnection(None, 'server', {'agent_name': 'server'})
        with TestClient(app) as client, patch.object(servers, 'manager', manager), patch.object(routes.manager, 'broadcast_to_dashboards', new=AsyncMock()):
            response = client.put('/api/servers/server/profile', json={'display_name': 'Elastic Production'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNotNone(manager.get_agent('server'))
            with Session(self.engine) as fresh:
                self.assertEqual(fresh.get(AgentProfile, 'server').display_name, 'Elastic Production')
            payload = {'name': 'Version', 'patterns': 'elastic', 'expected_tag': '9.3.1'}
            response = client.post('/api/inventory-rules', json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            rule_id = response.json()['id']
            self.assertEqual(client.put(f'/api/inventory-rules/{rule_id}', json={**payload, 'enabled': False}).status_code, 200)
            identity['role'] = 'user'
            self.assertEqual(client.post('/api/inventory-rules', json=payload).status_code, 403)
            self.assertEqual(client.delete(f'/api/inventory-rules/{rule_id}').status_code, 403)
            self.assertEqual(client.put('/api/servers/server/profile', json={'display_name': 'No'}).status_code, 403)
            identity['role'] = 'admin'
            self.assertEqual(client.post('/api/inventory-rules', json={**payload, 'expected_tag': 'invalid/tag'}).status_code, 400)
            self.assertEqual(client.delete(f'/api/inventory-rules/{rule_id}').status_code, 200)

    def test_rule_changes_recheck_current_online_containers_immediately(self):
        manager = WebSocketManager()
        manager.broadcast_to_dashboards = AsyncMock()
        conn = AgentConnection(Mock(), 'server', {})
        c = container()
        c.update(compose_metadata(c))
        conn.containers = [c]
        manager._agents['server'] = conn
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[routes.get_current_user_info] = lambda: {'username': 'admin', 'role': 'admin'}
        with TestClient(app) as client, patch.object(routes, 'manager', manager):
            payload = {'name': 'New policy', 'patterns': 'elastic', 'expected_tag': '9.4.0'}
            response = client.post('/api/inventory-rules', json=payload)
            self.assertEqual(response.status_code, 201, response.text)
            rule_id = response.json()['id']
            events = self.session.exec(select(AlertEvent).where(AlertEvent.rule_id == -rule_id)).all()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].status, 'active')
            self.assertEqual(client.put(f'/api/inventory-rules/{rule_id}', json={**payload, 'expected_tag': '9.2.6'}).status_code, 200)
            self.session.refresh(events[0])
            self.assertEqual(events[0].status, 'resolved')
            self.assertEqual(client.put(f'/api/inventory-rules/{rule_id}', json=payload).status_code, 200)
            active = self.session.exec(select(AlertEvent).where(AlertEvent.rule_id == -rule_id, AlertEvent.status == 'active')).all()
            self.assertEqual(len(active), 1)
            self.assertEqual(client.delete(f'/api/inventory-rules/{rule_id}').status_code, 200)
            self.session.refresh(active[0])
            self.assertEqual(active[0].status, 'resolved')


if __name__ == '__main__':
    unittest.main()
