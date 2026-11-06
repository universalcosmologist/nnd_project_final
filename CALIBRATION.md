# Agent Calibration Profiles

Corrective-action verification uses an approved healthy profile for the owning agent and affected metric target. A model prediction or a command exit code cannot approve a profile or establish that an issue is fixed. Missing or mismatched profiles leave verification inconclusive.

## Collect and Review a Healthy Window

The server stores the latest 100 metric samples per agent in `data/healing/agent_metrics_history.json`. The history is retained across agent reconnects and server restarts. It is a bounded working history, not a long-term archive.

Select a time window that an operator independently knows to be healthy. Do not select a window merely because the anomaly model raised no alert. The candidate builder requires at least 30 valid samples spanning at least five minutes, and verifies that all samples belong to the requested agent and target.

The agent measures latency and packet loss with 10 ping probes per sample. This reduces packet-loss resolution to 10 percentage-point increments compared with the previous four-probe measurement, at the cost of a longer sample cycle. Numeric verification requires five consecutive valid samples within the approved profile, received within a 120-second window. DNS and connectivity verification require three successful checks for the same target.

Generate a candidate from a reviewed time range, replacing the ISO timestamps and values for the target:

```powershell
python -m app.healing.ground_truth --agent-id agent-DEVICE --metric latency_ms --target-id icmp:8.8.8.8 --unit ms --since 2026-11-05T10:00:00 --until 2026-11-05T10:10:00 --confirm-healthy-window --output data/healing/candidate-latency.json
```

For packet loss use `--metric packet_loss_pct --unit percent`. For per-interface utilization use `--metric utilization_pct --target-id interface:Ethernet --unit percent`. The target must exactly match the `metric_targets` identity reported by the agent, or the interface name in its interface metrics.

The candidate command only writes a candidate; `approved` is always `false`. Review its sample count, time span, metric, target, unit, and calculated bound. If the selected period was not known healthy, discard the candidate and select another window.

## Bound Calculation

For nonnegative numeric metrics where larger values are worse, the upper healthy bound is:

`max(P95, median + 3 * 1.4826 * MAD)`

`MAD` is the median absolute deviation from the median. The percentile captures the observed upper tail; the robust-deviation term tolerates normal variation without allowing a few isolated outliers to inflate the range substantially. The calculation is performed separately for each agent and target. It does not impose a universal latency, loss, or utilization cutoff.

The agent's packet-loss measurement is quantized by the number of probes. Ten probes mean one lost response changes a sample by 10 percentage points. The calibrated bound cannot recover detail the probe does not measure; an increase in probe count would also lengthen the evidence window and should be treated as an explicit performance tradeoff.

## Approve a Profile

Merge the reviewed candidate's `profile` into `data/healing/calibration_profiles.json` under its agent and metric. Set `approved` to `true`, fill `approved_by` and `approved_at`, and increment `version` whenever replacing an existing profile. Keep the candidate's `target_id`, `unit`, calibration timestamps, sample count, and calculated statistics intact. The server accepts a profile only when it is approved, versioned, sufficiently sampled, and exactly matches the issue's agent, metric, and target.

Example document shape:

```json
{
  "schema_version": 1,
  "profiles": {
    "agent-DEVICE": {
      "metrics": {
        "latency_ms": {
          "target_id": "icmp:8.8.8.8",
          "unit": "ms",
          "method": "max(p95, median + 3 * 1.4826 * MAD)",
          "sample_count": 31,
          "median": 18.0,
          "p95": 22.0,
          "mad": 1.0,
          "upper_bound": 22.4478,
          "calibration_started_at": "2026-11-05T10:00:00",
          "calibration_completed_at": "2026-11-05T10:10:00",
          "approved": true,
          "approved_by": "operator-name",
          "approved_at": "2026-11-05T10:15:00",
          "version": 1
        }
      }
    }
  }
}
```

Profiles are independent across agents and targets. Never copy a profile between machines unless an operator explicitly verifies that the target identity and measurement conditions are equivalent.

## Current Evidence Limits

The current agent probes DNS for `google.com` and TCP connectivity to `1.1.1.1:443`. They cannot verify a different DNS domain or service. Interface utilization can be matched by interface name. Routing-loop, device-failure, and security-breach outcomes remain inconclusive until the agent reports target-specific evidence for those conditions.

Do not delete `agent_metrics_history.json` or existing issue/history data as part of profile approval. Profile changes affect future verification; they do not rewrite previously recorded outcomes.