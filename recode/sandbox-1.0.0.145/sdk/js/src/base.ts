/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ConsoleLogger, Logger, LogLevel } from "@agent-infra/logger";
import type { ClientConfig, ApiResponse, AIORequestOption } from "./types";

export class BaseClient {
  protected baseUrl: string;
  protected timeout: number;
  protected retries: number;
  protected retryDelay: number;
  protected logger: Logger;

  constructor(config: ClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, ""); // Remove trailing slash
    this.timeout = config.timeout ?? 30000; // 30 seconds default
    this.retries = config.retries ?? 1;
    this.retryDelay = config.retryDelay || 500; // 500ms default

    this.logger =
      config.logger ||
      new ConsoleLogger("AioClient", config.logLevel ?? LogLevel.INFO);
  }

  public async healthCheck() {
    try {
      const resp = await fetch(`${this.baseUrl}`, {
        method: "HEAD",
      });

      if (!resp.ok) {
        let extra = "";

        if (resp.headers.get("content-type") === "application/json") {
          const body = (await resp.json()) as ApiResponse<unknown>;
          extra = body.message;
        } else {
          extra = await resp.text();
        }

        throw new Error(`HTTP ${resp.status}: ${resp.statusText}  ${extra}`);
      }

      return {
        msg: "success",
      };
    } catch (e) {
      this.logger.error(
        `AIO Sandbox does not start or setup normally, please visit ${this.baseUrl} to view service status, `,
        e
      );
      throw e;
    }
  }

  private abortCallback(timeout: number) {
    return () => {
      this.logger.error(
        `The operation was aborted by AbortController, cost overflow ${timeout}`
      );
    };
  }

  protected async sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  protected async fetchWithTimeout(
    url: string,
    options: AIORequestOption
  ): Promise<Response> {
    const controller = new AbortController();

    //Prefer to the request-level timeout
    const { timeout = this.timeout, ...requestInitOption } = options;
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    const callback = this.abortCallback(timeout);
    controller.signal.addEventListener("abort", callback);

    const start = performance.now();

    try {
      const response = await fetch(url, {
        headers: {
          "Content-Type": "application/json",
          ...requestInitOption.headers,
        },
        ...requestInitOption,
        signal: controller.signal,
      });

      return response;
    } catch (error) {
      throw error;
    } finally {
      this.logger.debug(
        "fetch cost: ",
        Math.floor(performance.now() - start),
        " ms"
      );
      controller.signal.removeEventListener("abort", callback);
      clearTimeout(timeoutId);
    }
  }

  // Function overload implementation
  protected async request<T>(
    path: string,
    options: AIORequestOption & { raw: true }
  ): Promise<Response>;
  protected async request<T>(
    path: string,
    options?: AIORequestOption
  ): Promise<ApiResponse<T>>;
  protected async request<T>(
    path: string,
    options?: AIORequestOption
  ): Promise<ApiResponse<T> | Response> {
    const url = `${this.baseUrl}/${path.replace(/^\//, "")}`;
    let lastError: Error;

    for (let attempt = 0; attempt <= this.retries; attempt++) {
      try {
        this.logger.debug(`Attempt ${attempt + 1} for ${url}`);

        const response = await this.fetchWithTimeout(url, options || {});

        if (options?.raw) {
          return response;
        }

        if (!response.ok) {
          let extra = "";

          if (response.headers.get("content-type") === "application/json") {
            const body = (await response.json()) as ApiResponse<T>;
            extra = JSON.stringify(body);
          }

          throw new Error(
            `HTTP ${response.status}: ${response.statusText}  ${extra}`
          );
        }

        const result = (await response.json()) as ApiResponse<T>;
        this.logger.debug(`Success for ${url}`, options);
        return result;
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));

        attempt > 0 &&
          this.logger.error(
            `Attempt ${attempt + 1} failed for ${url}:`,
            lastError.stack || lastError.message
          );

        if (attempt < this.retries) {
          await this.sleep(this.retryDelay * Math.pow(2, attempt)); // Exponential backoff
        }
      }
    }

    this.logger.error(`All attempts failed for ${url}:`, lastError!);
    throw lastError!;
  }
}
