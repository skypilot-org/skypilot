/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */
import type {
  ApiResponse,
  FileEditorParams,
  FileListParams,
  FileListResp,
  ShellViewResponse,
  JupyterExecuteParams,
  JupterExecuteResp,
  AIORequestOption,
} from "../types";
import type { ModuleDependencies } from "./types";

export class FileModule {
  constructor(private deps: ModuleDependencies) {}

  /**
   * File editor operations
   */
  async editor(params: FileEditorParams, option?: AIORequestOption) {
    return this.deps.request<string>("/v1/file/str_replace_editor", {
      method: "POST",
      body: JSON.stringify(params),
      ...option,
    });
  }

  /**
   * Download file
   */
  async download(
    params: {
      path: string;
    },
    option?: AIORequestOption
  ) {
    return this.deps.request<ShellViewResponse>("/v1/file/download", {
      method: "POST",
      body: JSON.stringify(params),
      ...option,
    });
  }

  /**
   * List files in directory
   */
  async list(
    {
      path,
      sort_by = "name",
      file_types = ["string"],
      recursive = false,
      show_hidden = false,
      sort_desc = false,
      include_permissions = false,
      include_size = true,
    }: FileListParams,
    option?: AIORequestOption
  ) {
    return this.deps.request<FileListResp>("/v1/file/list", {
      method: "POST",
      body: JSON.stringify({
        path,
        sort_by,
        sort_desc,
        file_types,
        recursive,
        show_hidden,
        include_permissions,
        include_size,
      }),
      ...option,
    });
  }

  /**
   * Execute Jupyter code
   */
  async jupyterExecute(
    params: JupyterExecuteParams,
    option?: AIORequestOption
  ) {
    const requestParams = {
      ...params,
      kernel_name: params.kernel_name || "python3",
      timeout: params.timeout || 30,
    };

    return this.deps.request<JupterExecuteResp>("/v1/jupyter/execute", {
      method: "POST",
      body: JSON.stringify(requestParams),
      ...option,
    });
  }
}
