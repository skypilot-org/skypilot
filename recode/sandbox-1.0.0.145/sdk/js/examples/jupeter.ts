import { AioClient } from "../src/aio";

async function main() {
  const client = new AioClient({
    baseUrl: "http://localhost:8080",
  });

  const c = await client.jupyterExecute({
    code: "a=1",
  });

  console.log(c);
}

main();
