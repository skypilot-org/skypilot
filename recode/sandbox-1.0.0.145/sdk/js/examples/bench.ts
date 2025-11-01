import { AioClient } from "../src/aio";

async function main() {
  const client = new AioClient({
    baseUrl: "http://localhost:8080",
  });

  const start = Date.now();
  const round = 20;

  for (let i = 0; i < round; i++) {
    const c = await client.shellExecWithPolling({
      command: `mkdir -p /home/gem/tic_tac_toe_js_${i} && echo "Creating tic-tac-toe project directory"`,
    });

    console.log("in loop: ", {
      sessionId: c.data.session_id,
      status: c.data.status,
      output: c.data.output,
    });
  }

  const cost = Date.now() - start;

  console.log("total cost: ", cost);
  console.log("avg cost: ", (cost / round).toFixed(2));
}

main();
