/**
 * Optional adapter for the existing Phase 4 browser simulation.
 * Coalesces high-frequency signals into bounded timestamped event batches.
 * Signal classes remain separate so synthetic test signals are never labeled as
 * physical observations. Local scene XYZ is never asserted to be WGS84.
 */
export function createLegacyTelemetryBridge({endpoint = '/api/observations', source = 'daedalus-phase4-browser', intervalMs = 500} = {}) {
  const buckets = new Map();
  let flushTimer = null;
  let stopped = false;
  let localPose = null;

  function classify(name) {
    if (name.startsWith('sim.')) return 'simulated';
    if (name.startsWith('pose.') || name === 'cam.person%') return 'model_estimate';
    if (name.startsWith('cam.') || name.startsWith('mic.')) return 'derived';
    // Only known device/host channels are treated as source observations.
    // Unregistered signal names are derived/unknown until a source schema is added.
    if (name.startsWith('hr.') || name.startsWith('serial.') ||
        /^(ws\.(cpu_pct|mem_pct|net_kbps|temp_c|sdr_|gr_|eeg_|emg_))/.test(name)) return 'observed';
    return 'derived';
  }

  async function flush() {
    flushTimer = null;
    if (stopped || buckets.size === 0) return;
    const batches = [...buckets.entries()];
    buckets.clear();
    const tasks = batches.map(async ([dataClass, batch]) => {
      const sampleTimes = Object.fromEntries(Object.entries(batch.times).map(([key, value]) => [key, new Date(value).toISOString()]));
      const payload = {
        id: crypto.randomUUID(),
        source,
        kind: 'browser.sensor_snapshot',
        data_class: dataClass,
        observed_at: new Date(batch.latestTime).toISOString(),
        coordinate_frame: 'daedalus-local-world',
        values: batch.values,
        units: {},
        quality: dataClass === 'observed' ? 'unknown' : 'low',
        metadata: {
          note: 'Coalesced timestamped browser signal snapshot from Daedalus Phase 4.',
          sample_times_utc: sampleTimes,
          local_player_position: localPose,
          classification_rule: 'sim.*=simulated; pose.* and cam.person%=model_estimate; cam.* and mic.*=derived; hr.*, serial.*, and registered ws device/host channels=observed; unknown names=derived.',
          privacy: 'No WGS84 location is asserted; local XYZ is not geographic latitude/longitude.',
        },
      };
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
          keepalive: true,
        });
        if (!response.ok) console.warn('Daedalus telemetry event rejected:', response.status, await response.text());
      } catch (error) {
        console.warn('Daedalus telemetry API unavailable:', error);
      }
    });
    await Promise.allSettled(tasks);
  }

  return {
    record(name, value, pose = null) {
      if (stopped || typeof value !== 'number' || !Number.isFinite(value)) return;
      const key = String(name).slice(0, 100);
      const dataClass = classify(key);
      let batch = buckets.get(dataClass);
      if (!batch) {
        batch = {values: Object.create(null), times: Object.create(null), latestTime: 0};
        buckets.set(dataClass, batch);
      }
      const sampledAt = Date.now();
      batch.values[key] = value;
      batch.times[key] = sampledAt;
      batch.latestTime = Math.max(batch.latestTime, sampledAt);
      if (pose && ['x', 'y', 'z'].every(k => Number.isFinite(pose[k]))) localPose = pose;
      if (!flushTimer) flushTimer = setTimeout(flush, intervalMs);
    },
    async flushNow() {
      if (flushTimer) clearTimeout(flushTimer);
      await flush();
    },
    stop() {
      stopped = true;
      if (flushTimer) clearTimeout(flushTimer);
      buckets.clear();
    },
  };
}
