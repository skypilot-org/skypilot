/*
 * Copyright (c) 2025 Bytedance, Inc. and its affiliates.
 * SPDX-License-Identifier: Apache-2.0
 */
import type {
  ApiResponse,
  BrowserInfoResp,
  CDPVersionResp,
  AIORequestOption,
} from "../types";
import { AIOAction } from "./browser.types";
import type { ModuleDependencies } from "./types";

export class BrowserModule {
  constructor(private deps: ModuleDependencies) {}

  /**
   * Get CDP version information
   */
  async cdpVersion(option?: AIORequestOption) {
    return this.deps.request<CDPVersionResp>("/cdp/json/version", {
      method: "GET",
      ...option,
    });
  }

  /**
   * Get browser information
   */
  async info(option?: AIORequestOption) {
    return this.deps.request<BrowserInfoResp>("/v1/browser/info", {
      method: "GET",
      ...option,
    });
  }
  /**
   * Take a screenshot of the browser
   */
  async screenshot(option?: Omit<AIORequestOption, "raw">) {
    return this.deps.request("/v1/browser/screenshot", {
      method: "GET",
      raw: true,
      ...option,
    });
  }
  /**
   * Get browser information
   */
  async actions(params: AIOAction, option?: AIORequestOption) {
    return this.deps.request<unknown>("/v1/browser/actions", {
      method: "POST",
      body: JSON.stringify(params),
      ...option,
    });
  }
}
