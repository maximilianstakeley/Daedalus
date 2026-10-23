"""Signal bridge for the sim: SDR radio, GNU Radio, EEG/EMG boards, system stats, social relay.
pip install websockets numpy            (always)
pip install pyrtlsdr                    (--sdr rtl, an RTL-SDR dongle)
pip install pyzmq                       (--zmq, a GNU Radio ZMQ PUB Sink of float32 samples)
pip install brainflow                   (--board synthetic|cyton|ganglion|muse_2 ... for EEG/EMG)
pip install psutil                      (--stats)
Example: python bridge2.py --sdr rtl --freq 100.1e6 --board synthetic --stats --social
In the sim, click the WebSocket button (ws://localhost:8765), press J to join the social relay."""
import argparse, asyncio, json, threading, time
import numpy as np
import websockets

ap = argparse.ArgumentParser()
ap.add_argument("--sdr", choices=["none", "rtl"], default="none")
ap.add_argument("--freq", type=float, default=100.1e6, help="SDR center frequency in Hz")
ap.add_argument("--zmq", default="", help="GNU Radio ZMQ PUB Sink address, e.g. tcp://127.0.0.1:5555")
ap.add_argument("--board", default="", help="BrainFlow board name, e.g. synthetic, cyton, ganglion, muse_2")
ap.add_argument("--port", default="", help="serial port of the board, e.g. COM3 or /dev/ttyUSB0")
ap.add_argument("--stats", action="store_true")
ap.add_argument("--social", action="store_true", help="accept LAN clients on the presence relay (port 8766)")
a = ap.parse_args()
latest = {}

def sdr_loop():
    from rtlsdr import RtlSdr
    s = RtlSdr(); s.sample_rate = 2.4e6; s.center_freq = a.freq; s.gain = "auto"
    while True:
        x = s.read_samples(256 * 1024)[:65536]
        db = 10 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(len(x))))) ** 2 + 1e-12)
        latest["sdr_power_db"] = float(db.mean()); latest["sdr_peak_db"] = float(db.max())
        latest["sdr_peak_mhz"] = float((a.freq + (db.argmax() / len(db) - .5) * 2.4e6) / 1e6)
        time.sleep(.05)

def zmq_loop():
    import zmq
    s = zmq.Context().socket(zmq.SUB); s.connect(a.zmq); s.setsockopt(zmq.SUBSCRIBE, b"")
    while True:
        v = np.frombuffer(s.recv(), dtype=np.float32)
        if v.size:
            latest["gr_mean"] = float(v.mean()); latest["gr_max"] = float(v.max()); latest["gr_argmax"] = float(v.argmax())

def bio_loop():
    from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds
    from brainflow.data_filter import DataFilter
    p = BrainFlowInputParams(); p.serial_port = a.port
    bid = getattr(BoardIds, a.board.upper() + "_BOARD").value
    b = BoardShim(bid, p); b.prepare_session(); b.start_stream()
    ch = BoardShim.get_eeg_channels(bid); fs = BoardShim.get_sampling_rate(bid)
    n = DataFilter.get_nearest_power_of_two(fs * 2); time.sleep(2.5)
    while True:
        d = b.get_current_board_data(n)
        if d.shape[1] >= n:
            bands, _ = DataFilter.get_avg_band_powers(d, ch, fs, True)
            for k, v in zip(["delta", "theta", "alpha", "beta", "gamma"], bands): latest["eeg_" + k] = float(v)
            latest["eeg_focus"] = float(bands[3] / (bands[1] + bands[2] + 1e-9))
            x = d[ch[0]]; latest["emg_rms"] = float(np.sqrt(np.mean((x - x.mean()) ** 2)))
        time.sleep(.2)

def stats_loop():
    import psutil
    while True:
        latest["cpu_pct"] = psutil.cpu_percent(); latest["mem_pct"] = psutil.virtual_memory().percent; time.sleep(.5)

def run(fn):
    try: fn()
    except Exception as e: print(fn.__name__, "failed:", e)

feeds, room = set(), set()
async def feed(ws):
    feeds.add(ws)
    try: await ws.wait_closed()
    finally: feeds.discard(ws)
async def relay(ws):
    room.add(ws)
    try:
        async for m in ws: websockets.broadcast(room - {ws}, m)
    finally: room.discard(ws)
async def push():
    while True:
        if feeds and latest: websockets.broadcast(feeds, json.dumps(latest))
        await asyncio.sleep(.1)
async def main():
    for fn, on in [(sdr_loop, a.sdr == "rtl"), (zmq_loop, a.zmq), (bio_loop, a.board), (stats_loop, a.stats)]:
        if on: threading.Thread(target=run, args=(fn,), daemon=True).start()
    async with websockets.serve(feed, "localhost", 8765), websockets.serve(relay, "0.0.0.0" if a.social else "localhost", 8766):
        print("bridge running: signals ws://localhost:8765, presence relay ws://localhost:8766"); await push()
asyncio.run(main())
