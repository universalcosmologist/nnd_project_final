# Verified Detection Learning

The existing Isolation Forest is unsupervised. It does not learn from labels, and its old feedback method only logged messages. The supervised detector is now a separate, optional Random Forest candidate that consumes verified labels and runs in shadow mode on every agent measurement when calibrated relative features are available.

## Label Semantics

A detection label answers whether an anomaly was present in the input measurement. It is not the same as whether a corrective action succeeded.

- `false_positive`: an anomaly was predicted, but the approved healthy profile proved the triggering measurement was already healthy. The system records this label and does not dispatch an action.
- `true_positive`: the approved profile proved the pre-action metric was unhealthy, and post-action evidence verified recovery.
- `false_negative`: an independent operator, incident, or monitoring source verified an anomaly for a measurement the detector did not flag.
- `true_negative`: an independent source verified that a normal measurement was not anomalous.
- `inconclusive`: never used as a training label.

The current automatic lifecycle can produce false-positive and true-positive labels when it has a detection ID and approved profile evidence. False negatives/true negatives require an independent source. Integrations should call `AnomalyDetector.record_feedback(detection_id, is_real_anomaly, source, verifier, issue_id=None)` only after that source verifies the actual class. The observation ID is stored with each persisted agent metrics sample as `detection_id`. A source description and verifier identity are required; there is deliberately no unauthenticated feedback endpoint.

For operator review, list recent detections and their IDs:

```powershell
python -m app.models.supervised_detector --list --limit 20
```

After independently verifying a referenced incident, submit a label:

```powershell
python -m app.models.supervised_detector --detection-id DETECTION_ID --actual-anomaly true --source "incident INC-123" --verifier operator-name --confirm-verified
```

Without `--confirm-verified`, the CLI exits without writing. Use `--actual-anomaly false` only when independent evidence confirms normal behavior. The CLI records the source/verifier and may fit an offline shadow candidate; it never changes live issue decisions.

## Features and Training Gate

The candidate uses calibrated ratios for latency, packet loss, and bandwidth: observed value divided by that agent/target's approved healthy upper bound. Availability indicators distinguish a missing profile from a zero ratio. Samples with no calibrated relative features are excluded from supervised training.

At least 20 verified labels per class are required. Candidate evaluation first tries an unseen-agent holdout when both classes remain represented in training and holdout; otherwise it uses the latest 25% of timestamp-ordered labels. If both classes are not represented in both sides, training waits for more suitable data. It records balanced accuracy, a confusion matrix, and the split strategy. A candidate is saved only when holdout balanced accuracy reaches 70%. The candidate is never promoted automatically. It remains shadow-only and cannot create/close issues or override the deterministic evidence evaluator.

Each shadow prediction records its confidence and a drift score relative to the candidate's training feature distribution. Verified labels are summarized by prediction source with error rate, false-positive, and false-negative counts. Repeated false positives from the supervised candidate reduce its reported confidence, down to a 50% multiplier floor. These signals are diagnostic only; they do not change action or verification policy.

Current candidate files and labels:

- `data/models/verified_anomaly_labels.json`
- `data/models/supervised_anomaly_candidate.pkl`

Only verified labels enter the training set. Correctly predicted examples and verified model mistakes can both contribute. The case type (`true_positive`, `true_negative`, `false_positive`, or `false_negative`) is retained so model error can be measured separately from action effectiveness.

## Limits and Next Steps

The supervised candidate is presently shadow-only. Isolation Forest remains the active candidate source until there are enough verified labels and a later evaluation/promotion decision. It is optional as a novelty detector; it is not a truth authority or a required gate. A future step may promote a supervised model as primary after time- and agent-separated evaluation, then keep or retire Isolation Forest based on measured novelty value.

The history is bounded to the latest 5,000 detection observations plus labeled observations. The first implementation does not add a public feedback API; independent integrations should call the verified-label method from a trusted path. Historical resolution records that lack detection IDs and verified labels are not backfilled automatically.
