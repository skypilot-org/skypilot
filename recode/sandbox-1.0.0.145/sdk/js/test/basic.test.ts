/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AioClient } from "../src/aio";
import type { ClientConfig } from "../src/types";

describe("Timeout Effects Test Suite", () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
    mockFetch = vi.fn() as any;
    global.fetch = mockFetch as any;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  describe("Instance-level timeout", () => {
    it("should use instance timeout when no request timeout is specified", async () => {
      const instanceTimeout = 1000; // 1 second
      const config: ClientConfig = {
        baseUrl: "http://localhost:3000",
        timeout: instanceTimeout,
      };

      const client = new AioClient(config);

      // Mock fetch to properly handle AbortController
      mockFetch.mockImplementation((_url: string, options: any) => {
        return new Promise((resolve, reject) => {
          const responseTimer = setTimeout(() => {
            resolve(
              new Response(
                JSON.stringify({ success: true, data: {}, message: "ok" }),
                {
                  status: 200,
                  headers: { "Content-Type": "application/json" },
                }
              )
            );
          }, instanceTimeout + 500); // Delay longer than timeout

          // Listen for abort signal
          if (options?.signal) {
            options.signal.addEventListener("abort", () => {
              clearTimeout(responseTimer);
              reject(
                new DOMException("The operation was aborted.", "AbortError")
              );
            });
          }
        });
      });

      await expect(client.shellExec({ command: "echo test" })).rejects.toThrow(
        /aborted/i
      );
    });

    it("should successfully complete when response is within instance timeout", async () => {
      const instanceTimeout = 3000; // 3 seconds
      const config: ClientConfig = {
        baseUrl: "http://localhost:3000",
        timeout: instanceTimeout,
      };

      const client = new AioClient(config);

      // Mock a quick response within timeout
      mockFetch.mockResolvedValue(
        new Response(
          JSON.stringify({
            success: true,
            data: {
              session_id: "test-123",
              command: "echo test",
              status: "completed",
              returncode: 0,
              output: "test",
              console: [],
            },
            message: "ok",
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }
        )
      );

      const result = await client.shellExec({ command: "echo test" });

      expect(result.success).toBe(true);
      expect(result.data.command).toBe("echo test");
    });
  });

  describe("Request-level timeout", () => {
    it("should override instance timeout with request-level timeout", async () => {
      const instanceTimeout = 5000; // 5 seconds
      const requestTimeout = 1000; // 1 second

      const config: ClientConfig = {
        baseUrl: "http://localhost:3000",
        timeout: instanceTimeout,
      };

      const client = new AioClient(config);

      // Mock fetch to properly handle AbortController
      mockFetch.mockImplementation((_url: string, options: any) => {
        return new Promise((resolve, reject) => {
          const responseTimer = setTimeout(() => {
            resolve(
              new Response(
                JSON.stringify({ success: true, data: {}, message: "ok" }),
                {
                  status: 200,
                  headers: { "Content-Type": "application/json" },
                }
              )
            );
          }, requestTimeout + 500); // Delay longer than request timeout

          // Listen for abort signal
          if (options?.signal) {
            options.signal.addEventListener("abort", () => {
              clearTimeout(responseTimer);
              reject(
                new DOMException("The operation was aborted.", "AbortError")
              );
            });
          }
        });
      });

      await expect(
        client.shellExec({ command: "echo test" }, { timeout: requestTimeout })
      ).rejects.toThrow(/aborted/i);
    });

    it("should successfully complete when response is within request timeout", async () => {
      const instanceTimeout = 2000; // 2 seconds
      const requestTimeout = 4000; // 4 seconds (longer than instance)

      const config: ClientConfig = {
        baseUrl: "http://localhost:3000",
        timeout: instanceTimeout,
      };

      const client = new AioClient(config);

      // Mock a response that would exceed instance timeout but within request timeout
      const responseDelay = 3000;
      mockFetch.mockImplementation(
        () =>
          new Promise((resolve) =>
            setTimeout(() => {
              resolve(
                new Response(
                  JSON.stringify({
                    success: true,
                    data: {
                      session_id: "test-456",
                      command: "sleep 3",
                      status: "completed",
                      returncode: 0,
                      output: "done",
                      console: [],
                    },
                    message: "ok",
                  }),
                  {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                  }
                )
              );
            }, responseDelay)
          )
      );

      const result = await client.shellExec(
        { command: "sleep 3" },
        { timeout: requestTimeout }
      );

      expect(result.success).toBe(true);
      expect(result.data.command).toBe("sleep 3");
    });
  });

  describe("Edge cases", () => {
    it("should handle zero timeout", async () => {
      const config: ClientConfig = {
        baseUrl: "http://localhost:3000",
        timeout: 0,
      };

      const client = new AioClient(config);

      mockFetch.mockImplementation((_url: string, options: any) => {
        return new Promise((resolve, reject) => {
          const responseTimer = setTimeout(() => {
            resolve(
              new Response(
                JSON.stringify({ success: true, data: {}, message: "ok" })
              )
            );
          }, 1);

          if (options?.signal) {
            options.signal.addEventListener("abort", () => {
              clearTimeout(responseTimer);
              reject(
                new DOMException("The operation was aborted.", "AbortError")
              );
            });
          }
        });
      });

      await expect(client.shellExec({ command: "echo test" })).rejects.toThrow(
        /aborted/i
      );
    });
  });
});
