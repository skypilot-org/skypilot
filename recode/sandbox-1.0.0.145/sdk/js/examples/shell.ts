import { LogLevel } from "@agent-infra/logger";
import { AioClient } from "../src/aio";

async function main() {
  const client = new AioClient({
    baseUrl: "http://localhost:8080",
    logLevel: LogLevel.DEBUG,
    retries: 0,
  });

  const p = await client.shellExec({
    command: "ls -al",
  });

  // const p = await client.healthCheck();
  console.log(p);
}

main();
