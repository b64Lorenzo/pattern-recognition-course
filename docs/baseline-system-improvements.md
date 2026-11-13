# Baseline system and improvement status

## Current baseline candidate

The repository implements an online Rosenblatt perceptron with pre-operative inputs configured by the model XML (the default XML omits `operation_diagnosis_proxy`), numeric standardization and categorical one-hot encoding fitted on training rows, a seeded holdout, and aggregate mislabeling/confusion metrics.

## Implemented workflow

- One patient-level feature row per patient filename family (one or more CSVs), with postoperative targets stored separately.
- Joins are checked by unique `patient_id`, never row order.
- Validation output contains predictions without a true-target column; held-out outcomes are used only for aggregate evaluation.
- Missing BMI is derived when weight and height are available, subject to configured units.
- Binary targets are assigned directly from per-parameter success/failure criteria. Failure evidence takes precedence; missing or unmatched criteria remain unclassified (`NA`). The AKI-stage and mortality-flag criteria are provisional; a separate user-defined recorded-death-days rule marks nonnegative numeric values as death without a verified follow-up horizon. Eight parameter criteria await clinical definition.
- No reward scores, weighted aggregation, or severity bands are used.
- Synthetic tests cover extraction, binary classification, BMI derivation, split behavior, and metrics.

## Evaluation status

No benchmark comparison or cohort-level performance result is recorded. Synthetic tests do not establish predictive validity. Before claiming an improvement, define a comparator and evaluation protocol, confirm labels and input availability, prevent patient/event leakage, and evaluate on an appropriate held-out cohort. Verify outcome rules and BMI units with domain owners.
