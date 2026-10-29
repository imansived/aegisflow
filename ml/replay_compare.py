"""R1 measurement 2: how different is real device traffic from the simulator's? (docs/preregistration/R1-iot23-replay.md)

  python -m ml.replay_compare data/audit/r1_dev_A.json [data/audit/r1_held_A.json]
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy import stats

from ml.dataset import RowSet
from simulator.config import SimConfig
from simulator.fleet import Fleet


def sim_ticks(hours: float = 30.0, seed: int = 10, devices: int = 10) -> dict:
    cfg = SimConfig(seed=seed, n_devices=devices)
    sent, recv, conns, ends = [], [], [], []
    for t in Fleet(cfg, []).run(timedelta(hours=hours)):
        if t.timestamp.timestamp() < cfg.start.timestamp() + 6 * 3600:  # skip the first hours
            continue
        sent.append(t.bytes_sent); recv.append(t.bytes_received); conns.append(t.network_connections); ends.append(len(t.remote_endpoints or []))
    return {"sent": np.array(sent), "recv": np.array(recv), "conns": np.array(conns), "ends": np.array(ends)}


def q(x: np.ndarray) -> dict:
    return {f"p{p}": float(np.quantile(x, p / 100)) for p in (50, 90, 99)}


def main() -> None:
    real = {"sent": [], "recv": [], "conns": []}
    soft_frac = []
    for path in sys.argv[1:]:
        for c in json.loads(Path(path).read_text())["captures"]:
            f = c.get("_features")
            if f:
                for k in real:
                    real[k].append(np.array(f[k], np.int64))
            soft_frac.append((c["capture"], c["soft_score_benign"]["frac_gt_0.3"], c["judged_benign_device_days"]))
    real = {k: np.concatenate(v) for k, v in real.items()}
    sim = sim_ticks()
    rs = RowSet.load(Path("data/rows/seed10.npz"))
    ordinary = (rs.scenario == -1) & ~rs.ignore & ~rs.learning
    sim_soft = rs.raw_soft[ordinary][:, 1]
    out = {"real_ticks": int(len(real["sent"])), "sim_ticks": int(len(sim["sent"])),
           "fraction_of_ticks_with_traffic": {"real": float((real["conns"] > 0).mean()), "sim": float((sim["conns"] > 0).mean())}, "features": {},
           "soft_score_network": {"sim_ordinary_frac_gt_0.3": float((sim_soft > 0.3).mean()), "real_per_capture_frac_gt_0.3": soft_frac}}
    print(f"real benign ticks: {out['real_ticks']:,}   simulated ordinary ticks: {out['sim_ticks']:,}")
    print(f"ticks with any traffic: real {out['fraction_of_ticks_with_traffic']['real']:.3f}   simulated {out['fraction_of_ticks_with_traffic']['sim']:.3f}")
    print(f"{'feature':26s} {'KS distance':>12s}   real p50/p90/p99                       simulated p50/p90/p99")
    for key, nm, tr in (("sent", "bytes sent (log1p)", np.log1p), ("recv", "bytes received (log1p)", np.log1p), ("conns", "connections per tick", lambda x: x.astype(float))):
        a, b = tr(real[key]), tr(sim[key])
        ks = stats.ks_2samp(a, b).statistic
        qa, qb = q(real[key]), q(sim[key])
        out["features"][key] = {"ks": float(ks), "real": qa, "sim": qb}
        print(f"{nm:26s} {ks:12.3f}   {qa['p50']:>10.0f} {qa['p90']:>10.0f} {qa['p99']:>12.0f}   {qb['p50']:>10.0f} {qb['p90']:>10.0f} {qb['p99']:>12.0f}")
    print(f"network soft score above 0.3 on ordinary/benign ticks: simulator {out['soft_score_network']['sim_ordinary_frac_gt_0.3']:.4f}")
    for n, f, dd in soft_frac:
        print(f"   real {n:34s} {f if f is None else round(f, 4)}  ({dd} benign device-days)")
    Path("data/audit/r1_compare.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
