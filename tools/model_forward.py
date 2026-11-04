"""Forward a TCP port on the Docker bridge to a model server that listens on the host's loopback only
(deploy/compose/e2e.sh MODEL=live). Binds only the given address (by default the docker0 gateway), so
the model server is reachable from containers and from nothing else.

  python3 tools/model_forward.py LISTEN_HOST:PORT TARGET_HOST:PORT
"""
import asyncio
import sys


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main(listen, target):
    lh, lp = listen.rsplit(":", 1)
    th, tp = target.rsplit(":", 1)

    async def handle(client_r, client_w):
        try:
            up_r, up_w = await asyncio.open_connection(th, int(tp))
        except OSError:
            client_w.close()
            return
        await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))

    server = await asyncio.start_server(handle, lh, int(lp))
    print(f"forwarding {listen} -> {target}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
