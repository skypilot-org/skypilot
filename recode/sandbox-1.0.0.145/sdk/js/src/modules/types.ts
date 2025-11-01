/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */
import type { Logger } from "@agent-infra/logger";
import type { ApiResponse, AIORequestOption } from "../types";

export interface ModuleDependencies {
  request<T>(
    path: string,
    options: AIORequestOption & { raw: true }
  ): Promise<Response>;
  request<T>(path: string, options?: AIORequestOption): Promise<ApiResponse<T>>;

  logger: Logger;
  sleep: (ms: number) => Promise<void>;
}
