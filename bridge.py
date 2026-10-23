# Streams real machine signals to the sim: pip install websockets psutil, then python bridge.py
import asyncio, json, time, psutil, websockets

async def stream(ws):
    last, t0 = psutil.net_io_counters(), time.time()
    while True:
        n, t = psutil.net_io_counters(), time.time()
        kbps = (n.bytes_recv + n.bytes_sent - last.bytes_recv - last.bytes_sent) * 8 / 1024 / max(t - t0, 1e-3)
        msg = {"cpu_pct": psutil.cpu_percent(), "mem_pct": psutil.virtual_memory().percent, "net_kbps": kbps}
        try:
            temps = psutil.sensors_temperatures()
            if temps: msg["temp_c"] = next(iter(temps.values()))[0].current
        except Exception:
            pass
        last, t0 = n, t
        await ws.send(json.dumps(msg))
        await asyncio.sleep(0.2)

async def main():
    async with websockets.serve(stream, "localhost", 8765):
        await asyncio.Future()

asyncio.run(main())
