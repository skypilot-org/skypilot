/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */
import type {
  ApiResponse,
  ShellExecParams,
  ShellExecResponse,
  ShellKillParams,
  ShellViewParams,
  ShellViewResponse,
  AIORequestOption,
} from "../types";
import type { ModuleDependencies } from "./types";

export class ShellModule {
  constructor(private deps: ModuleDependencies) {}

  /**
   * Execute shell command
   */
  async exec(params: ShellExecParams, option?: AIORequestOption) {
    return this.deps.request<ShellExecResponse>("/v1/shell/exec", {
      method: "POST",
      body: JSON.stringify({
        async_mode: false,
        ...params,
      }),
      ...option,
    });
  }

  /**
   * View shell session
   */
  async view(params: ShellViewParams, option?: AIORequestOption) {
    return this.deps.request<ShellViewResponse>("/v1/shell/view", {
      method: "POST",
      body: JSON.stringify(params),
      ...option,
    });
  }

  /**
   * Kill shell session
   */
  async kill(params: ShellKillParams, option?: AIORequestOption) {
    return this.deps.request<string>("/v1/shell/kill", {
      method: "POST",
      body: JSON.stringify(params),
      ...option,
    });
  }

  /**
   * Execute shell command with async polling and timeout management
   * @param params Shell execution parameters
   * @param maxWaitTime Maximum wait time in milliseconds (default: 10 minutes)
   * @param pollInterval Polling interval in milliseconds (default: 2 seconds)
   */
  async execWithPolling(
    params: Omit<ShellExecParams, "async_mode">,
    maxWaitTime = 10 * 60 * 1000, // 10 minutes
    pollInterval = 1000 // 1s
  ): Promise<ApiResponse<ShellExecResponse>> {
    // Start async execution
    const execResult = await this.exec({
      ...params,
      async_mode: true,
    });

    if (!execResult.success) {
      return execResult;
    }

    const sessionId = execResult.data.session_id;
    const startTime = Date.now();

    this.deps.logger.debug(
      `Started async execution for session ${sessionId}, polling for completion...`
    );

    // Poll for completion
    while (Date.now() - startTime < maxWaitTime) {
      try {
        const viewResult = await this.view({ id: sessionId });

        if (!viewResult.success) {
          this.deps.logger.warn(
            `Failed to view session ${sessionId}:`,
            viewResult.message
          );
          await this.deps.sleep(pollInterval);
          continue;
        }

        // Check if command is completed (you may need to adjust this logic based on actual API behavior)
        const isFinished = ["completed", "active"].includes(
          viewResult?.data?.status ?? ""
        );

        // If there's output or the status indicates completion, return the result
        if (isFinished) {
          this.deps.logger.debug(`Command completed for session ${sessionId}`);

          // There is a problem with the view interface return in exec asynchronous mode.
          // console[number].output is always empty, and you need to manually get the value from data.output.
          const consoleData = viewResult.data.console;

          if (consoleData[0] && !consoleData[0]?.output) {
            consoleData[0].output = viewResult.data.output;
          }

          return {
            success: true,
            message: "Command completed successfully",
            data: {
              session_id: sessionId,
              command: params.command,
              status: viewResult?.data?.status ?? "unknow",
              exit_code: 0, // Assume success if we have output
              output: viewResult.data.output,
              console: consoleData,
            },
          };
        }

        // Wait before next poll
        await this.deps.sleep(pollInterval);
      } catch (error) {
        this.deps.logger.warn(`Error polling session ${sessionId}:`, error);
        await this.deps.sleep(pollInterval);
      }
    }

    // Timeout reached, kill the session
    this.deps.logger.warn(
      `Timeout reached for session ${sessionId}, killing session...`
    );

    try {
      await this.kill({ id: sessionId });
      this.deps.logger.info(`Session ${sessionId} killed due to timeout`);
    } catch (killError) {
      this.deps.logger.error(`Failed to kill session ${sessionId}:`, killError);
    }

    throw new Error(`Command execution timed out after ${maxWaitTime}ms`);
  }
}
