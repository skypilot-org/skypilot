/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Logger, LogLevel } from "@agent-infra/logger";

export interface ApiResponse<T> {
  success: boolean;
  message: string;
  data: T;
}

export interface ShellExecParams {
  id?: string;
  exec_dir?: string;
  command: string;
  async_mode?: boolean;
}

export interface ShellExecResponse {
  session_id: string;
  command: string;
  status: string;
  exit_code: number | null;
  output: string | null;
  console: Array<{
    ps1: string;
    command: string;
    output: string;
  }>;
}

export interface ShellViewParams {
  id: string;
}

export interface ShellViewResponse {
  output: string;
  session_id: string;
  status: string;
  console: Array<{
    ps1: string;
    command: string;
    output: string;
  }>;
}

export interface ShellKillParams {
  id: string;
}

export interface JupyterExecuteParams {
  code: string;
  timeout?: number;
  kernel_name?: string;
  session_id?: string;
}

export interface JupterExecuteResp {
  kernel_name: string;
  session_id: string;
  status: string;
  execution_count: number;
  outputs: string[];
  code: string;
  msg_id: string;
}

export interface FileEditorParams {
  command: string;
  path: string;
  file_text?: string;
  old_str?: string;
  new_str?: string;
  insert_line?: number;
  view_range?: number[];
}

export interface FileListParams {
  path: string;
  recursive?: boolean;
  show_hidden?: boolean;
  file_types?: string[];
  max_depth?: number;
  include_size?: boolean;
  include_permissions?: boolean;
  sort_by?: string;
  sort_desc?: boolean;
}

export interface FileListResp {
  path: string;
  files: Array<{
    name: string;
    path: string;
    is_directory: boolean;
    size?: unknown;
    modified_time: string;
    permissions?: unknown;
    extension?: unknown;
  }>;
  total_count: number;
  directory_count: number;
  file_count: number;
}

export interface ClientConfig {
  baseUrl: string;
  timeout?: number;
  retries?: number;
  retryDelay?: number;
  logger?: Logger;
  logLevel?: LogLevel;
}

export interface AIORequestOption extends RequestInit {
  timeout?: number;
  // Whether to return to the original Response
  raw?: boolean;
}

export interface CDPVersionResp {
  Browser: string;
  "Protocol-Version": string;
  "User-Agent": string;
  "V8-Version": string;
  "WebKit-Version": string;
  webSocketDebuggerUrl: string;
}

export interface BrowserInfoResp {
  user_agent: string;
  cdp_url: string;
  vnc_url: string;
  viewport: {
    width: number;
    height: number;
  };
}
