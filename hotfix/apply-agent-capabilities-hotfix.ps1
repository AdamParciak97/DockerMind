$ErrorActionPreference = 'Stop'

# Run from the DockerMind repository root on the host running the central container.
# This is an in-container hotfix; it is temporary and should be replaced by the next image build.
$container = if ($env:DOCKER_MIND_CONTAINER) { $env:DOCKER_MIND_CONTAINER } else { 'dockermind-web' }

docker cp 'central/routers/servers.py' "${container}:/app/routers/servers.py"
docker cp 'central/websocket_manager.py' "${container}:/app/websocket_manager.py"
docker cp 'central/static/fleet.js' "${container}:/app/static/fleet.js"
docker restart $container

Write-Host "Hotfix zastosowany w kontenerze $container. Agentów nie trzeba aktualizować."
Write-Host "Po restarcie wykonaj Ctrl+F5 w przeglądarce."
