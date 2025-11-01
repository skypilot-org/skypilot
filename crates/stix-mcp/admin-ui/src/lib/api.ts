import axios from 'axios';

const API_BASE_URL = '/api';

export interface Agent {
  id: string;
  name: string;
  permissions: string[];
  created_at: string;
  last_active: string;
}

export interface FileLock {
  file_path: string;
  owner_id: string;
  owner_name: string;
  locked_at: string;
  expires_at: string | null;
}

export interface LogEntry {
  id: string;
  agent_id: string;
  agent_name: string;
  tool_name: string;
  action: string;
  parameters: any;
  result: string | null;
  timestamp: string;
  duration_ms: number | null;
}

export interface WorkTarget {
  id: string;
  title: string;
  description: string;
  assigned_to: string | null;
  priority: 'low' | 'medium' | 'high';
  status: 'pending' | 'in-progress' | 'completed';
  created_at: string;
  updated_at: string;
}

export interface Stats {
  total_agents: number;
  active_agents: number;
  total_locks: number;
  total_logs: number;
  requests_today: number;
}

class ApiClient {
  private adminToken: string | null = null;

  setAdminToken(token: string) {
    this.adminToken = token;
    localStorage.setItem('admin_token', token);
  }

  getAdminToken(): string | null {
    if (!this.adminToken) {
      this.adminToken = localStorage.getItem('admin_token');
    }
    return this.adminToken;
  }

  clearAdminToken() {
    this.adminToken = null;
    localStorage.removeItem('admin_token');
  }

  private getHeaders() {
    const token = this.getAdminToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  // Health check
  async health() {
    const response = await axios.get(`${API_BASE_URL}/health`);
    return response.data;
  }

  // Admin login
  async adminLogin(password: string) {
    const response = await axios.post(`${API_BASE_URL}/admin/login`, { password });
    this.setAdminToken(response.data.token);
    return response.data;
  }

  // Agent management
  async listAgents(): Promise<Agent[]> {
    const response = await axios.get(`${API_BASE_URL}/admin/agents`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async createAgent(name: string, permissions: string[]) {
    const response = await axios.post(
      `${API_BASE_URL}/admin/agents`,
      { name, permissions },
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  async deleteAgent(agentId: string) {
    const response = await axios.delete(`${API_BASE_URL}/admin/agents/${agentId}`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async updateAgentPermissions(agentId: string, permissions: string[]) {
    const response = await axios.patch(
      `${API_BASE_URL}/admin/agents/${agentId}/permissions`,
      { permissions },
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  async regenerateToken(agentId: string) {
    const response = await axios.post(
      `${API_BASE_URL}/admin/agents/${agentId}/regenerate-token`,
      {},
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  // Locks management
  async listLocks(): Promise<FileLock[]> {
    const response = await axios.get(`${API_BASE_URL}/admin/locks`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async forceUnlock(filePath: string) {
    const response = await axios.post(
      `${API_BASE_URL}/admin/locks/force-unlock`,
      { file_path: filePath },
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  // Logs
  async listLogs(limit?: number): Promise<LogEntry[]> {
    const response = await axios.get(`${API_BASE_URL}/admin/logs`, {
      params: { limit },
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async getAgentLogs(agentId: string): Promise<LogEntry[]> {
    const response = await axios.get(`${API_BASE_URL}/admin/agents/${agentId}/logs`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  // Stats
  async getStats(): Promise<Stats> {
    const response = await axios.get(`${API_BASE_URL}/admin/stats`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  // Work Targets
  async listWorkTargets(): Promise<WorkTarget[]> {
    const response = await axios.get(`${API_BASE_URL}/admin/work-targets`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async createWorkTarget(data: Partial<WorkTarget>) {
    const response = await axios.post(
      `${API_BASE_URL}/admin/work-targets`,
      data,
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  async updateWorkTarget(id: string, data: Partial<WorkTarget>) {
    const response = await axios.patch(
      `${API_BASE_URL}/admin/work-targets/${id}`,
      data,
      { headers: this.getHeaders() }
    );
    return response.data;
  }

  async deleteWorkTarget(id: string) {
    const response = await axios.delete(`${API_BASE_URL}/admin/work-targets/${id}`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  // Configuration
  async getConfig() {
    const response = await axios.get(`${API_BASE_URL}/admin/config`, {
      headers: this.getHeaders(),
    });
    return response.data;
  }

  async updateConfig(config: any) {
    const response = await axios.patch(
      `${API_BASE_URL}/admin/config`,
      config,
      { headers: this.getHeaders() }
    );
    return response.data;
  }
}

export const apiClient = new ApiClient();
