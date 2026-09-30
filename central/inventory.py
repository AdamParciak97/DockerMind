"""Compose image metadata and container inventory policies."""
import yaml


def image_tag(image):
    if not isinstance(image, str) or not image or '$' in image:
        return None
    if image.startswith('sha256:'):
        return None
    reference, separator, _ = image.partition('@')
    leaf = reference.rsplit('/', 1)[-1]
    if ':' in leaf:
        return leaf.rsplit(':', 1)[1] or None
    return None if separator else 'latest'


def compose_metadata(container):
    content = container.get('compose')
    result = {'compose_image': '', 'compose_tag': None, 'compose_error': 'Brak pliku Compose'}
    if not isinstance(content, str) or not content:
        return result
    config_files = (container.get('labels') or {}).get('com.docker.compose.project.config_files', '')
    if len([p for p in config_files.split(',') if p.strip()]) > 1:
        return {**result, 'compose_error': 'Wiele plików Compose: brak scalonej konfiguracji'}
    if len(content) > 1024 * 1024:
        return {**result, 'compose_error': 'Plik Compose jest zbyt duży'}
    try:
        doc = yaml.safe_load(content)
        services = doc.get('services', {}) if isinstance(doc, dict) else {}
        service = (container.get('labels') or {}).get('com.docker.compose.service')
        config = services.get(service) if isinstance(services, dict) else None
        if not isinstance(config, dict):
            return {**result, 'compose_error': 'Nie znaleziono usługi Compose'}
        image = config.get('image')
        if not isinstance(image, str) or not image:
            return {**result, 'compose_error': 'Brak image w usłudze Compose'}
        tag = image_tag(image)
        return {'compose_image': image, 'compose_tag': tag,
                'compose_error': '' if tag else 'Nie można ustalić tagu (zmienna lub digest)'}
    except (yaml.YAMLError, ValueError, RecursionError):
        return {**result, 'compose_error': 'Nieprawidłowy plik Compose'}


def matches(rule, container):
    fields = [container.get('name', '')] if rule.field == 'name' else [container.get('image', ''), container.get('compose_image', '')]
    return any(p.strip().casefold() in str(value).casefold()
               for p in rule.patterns.splitlines() if p.strip() for value in fields)


def evaluate_inventory(session, agent_id, containers):
    from sqlmodel import select
    from models import AlertEvent, InventoryRule, AgentProfile
    rules = session.exec(select(InventoryRule).where(InventoryRule.enabled == True)).all()
    profile = session.get(AgentProfile, agent_id)
    agent_label = (profile.display_name if profile and profile.display_name else '') or agent_id
    active = session.exec(select(AlertEvent).where(
        AlertEvent.agent_id == agent_id, AlertEvent.metric == 'image_tag',
        AlertEvent.status.in_(['active', 'acknowledged']))).all()
    existing = {(e.rule_id, e.container_name): e for e in active}
    pending = set()
    alerts = []
    for rule in rules:
        if rule.agent_name and rule.agent_name.casefold() not in agent_label.casefold():
            continue
        if not rule.expected_tag:
            continue
        for c in containers:
            if not matches(rule, c):
                continue
            actual = c.get('compose_tag')
            if actual == rule.expected_tag:
                continue
            # Negative IDs separate inventory policies from numeric metric rules.
            key = (-rule.id, c.get('name', ''))
            pending.add(key)
            observed = actual or c.get('compose_error') or 'Brak danych Compose'
            message = f'{rule.name}: Compose: {observed}; wymagany tag: {rule.expected_tag}'
            if key in existing:
                existing[key].message = message
                session.add(existing[key])
                continue
            event = AlertEvent(rule_id=-rule.id, agent_id=agent_id, container_name=key[1],
                               metric='image_tag', value=1, threshold=1, message=message)
            session.add(event)
            alerts.append({'agent_id': agent_id, 'container_name': key[1], 'metric': 'image_tag',
                           'value': 1, 'threshold': 1, 'message': message})
    for key, event in existing.items():
        if key not in pending:
            event.status = 'resolved'
            session.add(event)
            alerts.append({'event': 'alert_resolved', 'agent_id': agent_id, 'container_name': event.container_name})
    session.commit()
    return alerts
