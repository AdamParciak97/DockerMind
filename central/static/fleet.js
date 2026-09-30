/* Fleet overview and bounded parallel command dispatch. No data persists in local storage. */
function fleetFeatures() {
  return {
    fleetSearch: '', fleetFilter: 'all', fleetGroup: '',
    commandText: '', commandTimeout: 30, commandRunning: false,
    commandResults: [], commandError: '', commandSnapshot: '', commandGeneration: 0,
    commandDryRun: true, commandHistory: [], commandTemplates: [], commandTemplateName: '',
    commandVariableText: '', commandHealth: [], commandSchedules: [], scheduleName: '', scheduleCron: '* * * * *',
    fleetRefreshing: false, fleetRefreshedAt: null,
    commandPresets: [
      {name: 'Stan hosta', command: 'hostname; uptime; df -h'},
      {name: 'Kontenery Docker', command: 'docker ps --format "table {{.Names}}\\t{{.Status}}\\t{{.Image}}"'},
      {name: 'Zużycie zasobów', command: 'docker stats --no-stream'},
    ],
    showFleet() {
      this.closeTerminal(); this.dCharts(); this.dSrvCharts();
      this.selSrvId = null; this.selCt = null; this.page = 'dashboard'; this.mobileNav = false;
      this.hostTerminal = false;
    },
    async refreshFleet() {
      if (this.fleetRefreshing) return;
      this.fleetRefreshing = true;
      try { const data = await this._get('/api/servers'); if (data) this.servers = data; this.fleetRefreshedAt = new Date(); }
      catch (e) { this.commandError = e.message; }
      finally { this.fleetRefreshing = false; }
    },
    showCommands() {
      this.closeTerminal(); this.dCharts(); this.dSrvCharts();
      this.selCt = null; this.page = 'commands'; this.mobileNav = false;
      this.hostTerminal = false;
      this.loadCommandTools();
    },
    get selectedServers() { return this.sortedServers.filter(s => this.bulkServers[s.agent_id]); },
    get automaticGroups() {
      const groups = new Map();
      for (const s of this.sortedServers) {
        const labels = [s.online ? 'Online' : 'Offline'];
        const os = s.info?.os || s.info?.operating_system;
        if (os) labels.push(`OS: ${os}`);
        const docker = String(s.info?.docker_version || '').match(/(\d+)/);
        if (docker) labels.push(`Docker ${docker[1]}`);
        for (const label of labels) {
          const id = `auto:${label}`;
          if (!groups.has(id)) groups.set(id, {id, name: `⚙ ${label}`, members: []});
          groups.get(id).members.push(s.agent_id);
        }
      }
      return [...groups.values()];
    },
    get availableFleetGroups() { return [...this.serverGroups, ...this.automaticGroups]; },
    get fleetServers() {
      const query = this.fleetSearch.trim().toLocaleLowerCase('pl');
      const group = this.availableFleetGroups.find(g => String(g.id) === this.fleetGroup);
      return this.sortedServers.filter(s => {
        if (group && !(group.members || []).includes(s.agent_id)) return false;
        if (this.fleetFilter === 'online' && !s.online) return false;
        if (this.fleetFilter === 'offline' && s.online) return false;
        if (this.fleetFilter === 'issues' && s.online && !this.fleetStats(s).issues) return false;
        return !query || [this.srvName(s), s.agent_id, s.info?.ip, s.info?.hostname,
          ...(s.containers || []).flatMap(c => [c.name, c.image])].join(' ').toLocaleLowerCase('pl').includes(query);
      });
    },
    fleetStats(s) {
      const containers = s.containers || [];
      const running = containers.filter(c => c.status === 'running');
      const cores = Number(s.info?.cpus) || 0;
      const memory = Number(s.info?.total_memory) || 0;
      const used = running.reduce((n, c) => n + (Number(c.memory?.usage_bytes) || 0), 0);
      return {
        running: running.length, total: containers.length,
        issues: containers.filter(c => c.status === 'restarting' || c.status === 'dead' || c.health === 'unhealthy' || c.health?.status === 'unhealthy' || (c.restart_count || 0) > 3).length,
        cpu: s.online && cores ? running.reduce((n, c) => n + (Number(c.cpu_percent) || 0), 0) / cores : null,
        ram: s.online && memory ? used / memory * 100 : null,
      };
    },
    get fleetSummary() {
      const online = this.servers.filter(s => s.online);
      return {online: online.length, offline: this.servers.length - online.length,
        running: online.reduce((n, s) => n + this.fleetStats(s).running, 0),
        total: online.reduce((n, s) => n + this.fleetStats(s).total, 0),
        issues: online.reduce((n, s) => n + this.fleetStats(s).issues, 0)};
    },
    get allFleetSelected() { return this.fleetServers.length > 0 && this.fleetServers.every(s => this.bulkServers[s.agent_id]); },
    get someFleetSelected() { return this.fleetServers.some(s => this.bulkServers[s.agent_id]) && !this.allFleetSelected; },
    toggleFleetSelection() {
      const select = !this.allFleetSelected;
      const next = {...this.bulkServers};
      for (const s of this.fleetServers) { if (select) next[s.agent_id] = true; else delete next[s.agent_id]; }
      this.bulkServers = next; this.bulkMode = true;
    },
    commandUnavailable(s) {
      if (!s?.online) return 'Serwer offline';
      if (s.info?.capabilities?.command_protocol !== 1) return 'Wymagana aktualizacja agenta';
      if (s.info?.capabilities?.host_commands !== true) return 'Dostęp do hosta wyłączony';
      return '';
    },
    get commandReadyCount() { return this.selectedServers.filter(s => !this.commandUnavailable(s)).length; },
    get commandCompleted() { return this.commandResults.filter(r => !['queued', 'running'].includes(r.status)).length; },
    commandStatus(result) {
      return {queued: 'Oczekuje', running: 'Wykonywanie…', success: 'Sukces', failed: 'Błąd', timeout: 'Limit czasu', skipped: 'Pominięty', unknown: 'Brak potwierdzenia'}[result.status] || result.status;
    },
    async runFleetCommand() {
      if (this.commandRunning || this.myRole !== 'admin') return;
      this.commandError = '';
      const command = this.commandText;
      const timeout = Number(this.commandTimeout);
      if (!command.trim() || command.length > 16000 || command.includes('\0')) { this.commandError = 'Wpisz komendę (maksymalnie 16000 znaków).'; return; }
      if (!Number.isInteger(timeout) || timeout < 1 || timeout > 120) { this.commandError = 'Limit czasu: od 1 do 120 sekund.'; return; }
      if (!this.commandReadyCount) { this.commandError = 'Wybierz przynajmniej jeden serwer gotowy do wykonania komend.'; return; }
      const targets = this.selectedServers.map(s => ({agent_id: s.agent_id, name: this.srvName(s), reason: this.commandUnavailable(s)}));
      const names = targets.filter(s => !s.reason).map(s => s.name).join(', ');
      if (this.commandDryRun) {
        try {
          const preview = await this._post('/api/commands/dry-run', {command, targets: targets.map(t => t.agent_id)});
          const unavailable = (preview.targets || []).filter(t => t.reason).map(t => `${t.name}: ${t.reason}`);
          const details = unavailable.length ? `\nPominięte:\n${unavailable.join('\n')}` : '';
          if (!confirm(`DRY RUN — cele:\n${names}${details}\n\nKomenda:\n${command}\n\nUruchomić teraz?`)) return;
        } catch (e) { this.commandError = `Dry run nieudany: ${e.message}`; return; }
      } else if (!confirm(`Wykonać na hostach: ${names}?\n\n${command}\n\nLimit: ${timeout} s na serwer.`)) return;
      this.commandSnapshot = command;
      const generation = ++this.commandGeneration;
      this.commandResults = targets.map(s => ({...s, status: s.reason ? 'skipped' : 'queued', stdout: '', stderr: '', error: s.reason}));
      this.commandRunning = true;
      const queue = this.commandResults.filter(r => r.status === 'queued');
      const worker = async () => {
        while (queue.length) {
          const row = queue.shift();
          if (!this.loggedIn || generation !== this.commandGeneration) { row.status = 'skipped'; row.error = 'Sesja zakończona.'; continue; }
          row.status = 'running';
          try {
            const data = await this._post(`/api/servers/${encodeURIComponent(row.agent_id)}/command`, {command, timeout, variables: this.commandVariables()});
            if (!data) throw new Error('Brak potwierdzenia z centrali.');
            Object.assign(row, data, {status: data.timed_out ? 'timeout' : data.exit_code === 0 ? 'success' : 'failed'});
          } catch (e) {
            row.status = e.status && e.status < 500 ? 'failed' : 'unknown';
            row.error = e.message + (row.status === 'unknown' ? ' Sprawdź stan hosta przed ponownym uruchomieniem.' : '');
          }
        }
      };
      try { await Promise.all(Array.from({length: Math.min(4, queue.length)}, worker)); }
      finally { if (generation === this.commandGeneration) this.commandRunning = false; }
    },
    commandVariables() {
      const variables = {};
      for (const line of this.commandVariableText.split(/\r?\n/)) {
        const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
        if (match) variables[match[1]] = match[2];
      }
      return variables;
    },
    async loadCommandTools() {
      try {
        const [history, templates, health, schedules] = await Promise.all([
          this._get('/api/command-history'), this._get('/api/command-templates'),
          this._get('/api/agent-health'), this.myRole === 'admin' ? this._get('/api/command-schedules') : Promise.resolve([])
        ]);
        if (history) this.commandHistory = history;
        if (templates) this.commandTemplates = templates;
        if (health) this.commandHealth = health;
        if (schedules) this.commandSchedules = schedules;
      } catch (e) { this.commandError = e.message; }
    },
    useCommandHistory(row) { this.commandText = row.command; this.commandTimeout = row.timeout; },
    useCommandTemplate(row) { this.commandText = row.command; this.commandTimeout = row.timeout; this.commandTemplateName = row.name; },
    async saveCommandTemplate() {
      if (!this.commandTemplateName.trim() || !this.commandText.trim()) return;
      try { await this._post('/api/command-templates', {name: this.commandTemplateName, command: this.commandText, timeout: this.commandTimeout, allowed_roles: 'admin'}); await this.loadCommandTools(); this._notify('Szablon zapisany.', 'success'); }
      catch (e) { this.commandError = e.message; }
    },
    async saveCommandSchedule() {
      if (!this.scheduleName.trim() || !this.commandText.trim()) return;
      try { await this._post('/api/command-schedules', {name: this.scheduleName, command: this.commandText, cron: this.scheduleCron, targets: this.selectedServers.map(s => s.agent_id), enabled: true}); this.scheduleName = ''; await this.loadCommandTools(); this._notify('Harmonogram zapisany.', 'success'); }
      catch (e) { this.commandError = e.message; }
    },
    async deleteCommandSchedule(id) {
      if (!confirm('Usunąć harmonogram?')) return;
      try { await fetch(`/api/command-schedules/${id}`, {method: 'DELETE', credentials: 'same-origin'}); await this.loadCommandTools(); } catch (e) { this.commandError = e.message; }
    },
    downloadFleetReport(format = 'csv') { window.location.href = `/api/reports/fleet.${format}`; },
    downloadCommandResults() {
      const blob = new Blob([JSON.stringify({command: this.commandSnapshot, results: this.commandResults}, null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob); const a = document.createElement('a');
      a.href = url; a.download = 'dockermind-command-results.json'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    get inventoryPreview() {
      if (!this.inventoryForm.patterns.trim()) return [];
      return this.sortedServers.filter(s => !this.inventoryForm.agent_name.trim() || this.srvName(s).toLocaleLowerCase('pl').includes(this.inventoryForm.agent_name.trim().toLocaleLowerCase('pl'))).flatMap(s => (s.containers || []).filter(c => this.inventoryMatches(this.inventoryForm, c))
        .map(c => ({key: `${s.agent_id}/${c.name}`, server: this.srvName(s), name: c.name, online: s.online,
          tag: c.compose_tag, error: c.compose_error, valid: !this.inventoryForm.expected_tag.trim() || c.compose_tag === this.inventoryForm.expected_tag.trim()})));
    },
    async _apiResponse(response) {
      if (response.status === 401) {
        this.logout();
        const error = new Error('Sesja wygasła. Zaloguj się ponownie.'); error.status = 401; throw error;
      }
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        const error = new Error('Serwer zwrócił nieprawidłową odpowiedź API. Sprawdź wersję centrali i odśwież aplikację.');
        error.status = response.status; throw error;
      }
      let data;
      try { data = await response.json(); }
      catch { throw new Error('Nie udało się odczytać odpowiedzi API. Odśwież aplikację.'); }
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.map(e => `${(e.loc || []).filter(x => x !== 'body').join('.')}: ${e.msg}`).join('; ') : data.detail;
        const error = new Error(detail || `HTTP ${response.status}`); error.status = response.status; throw error;
      }
      return data;
    },
  };
}
