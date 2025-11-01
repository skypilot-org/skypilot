/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */
import type {
  ClientConfig,
  ApiResponse,
  ShellExecParams,
  ShellKillParams,
  ShellViewParams,
  JupyterExecuteParams,
  FileEditorParams,
  FileListParams,
  AIORequestOption,
} from "./types";
import { BaseClient } from "./base";
import { ShellModule } from "./modules/shell";
import { FileModule } from "./modules/file";
import { BrowserModule } from "./modules/browser";
import { AIOAction } from "./modules/browser.types";

export class AioClient extends BaseClient {
  private shellModule: ShellModule;
  private fileModule: FileModule;
  private browserModule: BrowserModule;

  constructor(config: ClientConfig) {
    super(config);

    const deps = {
      request: this.request.bind(this),
      logger: this.logger,
      sleep: this.sleep.bind(this),
    };

    this.shellModule = new ShellModule(deps);
    this.fileModule = new FileModule(deps);
    this.browserModule = new BrowserModule(deps);
  }

  /**
   * Execute shell command
   */
  async shellExec(params: ShellExecParams, option?: AIORequestOption) {
    return this.shellModule.exec(params, option);
  }

  /**
   * Execute shell command with async polling and timeout management
   * @param params Shell execution parameters
   * @param maxWaitTime Maximum wait time in milliseconds (default: 10 minutes)
   * @param pollInterval Polling interval in milliseconds (default: 2 seconds)
   */
  async shellExecWithPolling(
    params: Omit<ShellExecParams, "async_mode">,
    maxWaitTime = 10 * 60 * 1000, // 10 minutes
    pollInterval = 1000 // 1s
  ) {
    return this.shellModule.execWithPolling(params, maxWaitTime, pollInterval);
  }

  /**
   * View shell session
   */
  async shellView(params: ShellViewParams, option?: AIORequestOption) {
    return this.shellModule.view(params, option);
  }

  /**
   * Kill shell session
   */
  async shellKill(params: ShellKillParams, option?: AIORequestOption) {
    return this.shellModule.kill(params, option);
  }

  /**
   * Execute Jupyter code
   */
  async jupyterExecute(
    params: JupyterExecuteParams,
    option?: AIORequestOption
  ) {
    return this.fileModule.jupyterExecute(params, option);
  }

  /**
   * File editor operations
   */
  async fileEditor(params: FileEditorParams, option?: AIORequestOption) {
    return this.fileModule.editor(params, option);
  }

  /**
   * Download file
   */
  async fileDownload(
    params: {
      path: string;
    },
    option?: AIORequestOption
  ) {
    return this.fileModule.download(params, option);
  }

  async fileList(params: FileListParams, option?: AIORequestOption) {
    return this.fileModule.list(params, option);
  }

  async cdpVersion(option?: AIORequestOption) {
    return this.browserModule.cdpVersion(option);
  }

  /**
   * Get browser information
   */
  async browserInfo(option?: AIORequestOption) {
    return this.browserModule.info(option);
  }

  /**
   * Get browser information
   */
  async browserScreenshot(option?: AIORequestOption) {
    return this.browserModule.screenshot(option);
  }

  /**
   * Get browser information
   */
  async browserActions(params: AIOAction, option?: AIORequestOption) {
    return this.browserModule.actions(params, option);
  }
}
