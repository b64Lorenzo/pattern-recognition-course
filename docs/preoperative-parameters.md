# Pre-operative parameters

This guide describes the pre-operative feature columns in `patient_vectors.csv`. Each patient may have one CSV or several same-directory numbered parts (for example, `ref_0002.csv` and `ref_0002_1.csv`); each output row represents one patient family. The machine file keeps stable parameter IDs as headers and contains no outcome label. Labels derived from postoperative evidence are stored in `patient_outcomes.csv` and matched by `patient_id` during training/evaluation.

## Features used

| CSV column | Parameter name | Naam (Nederlands) | Source field(s) | Notes |
|---|---|---|---|---|
| `age` | Age | Leeftijd | `epic.DEMOGRAPHICS.age` | Recorded age at surgery. |
| `sex` | Sex | Geslacht | `epic.DEMOGRAPHICS.sex` | Categorical value as recorded. |
| `weight` | Weight | Gewicht | `epic.DEMOGRAPHICS.gewicht` | Last valid pre-operative value; verify units in the source. |
| `height` | Height | Lengte | `epic.DEMOGRAPHICS.lengte` | Last valid pre-operative value; verify units in the source. |
| `bmi` | BMI | BMI | `epic.DEMOGRAPHICS.BMI` | Keep recorded pre-operative value; if missing, derive from weight and height using `config/pipeline.xml` units (default kg/cm; verify before clinical/research use). |
| `asa` | ASA physical status | ASA-classificatie | `epic.DEMOGRAPHICS.ASA` | Recorded pre-operative status. |
| `operation_diagnosis_proxy` | Operation / diagnosis proxy | Operatie-/diagnoseproxy | `epic.INGREEP.AN_PROC_NAME` | Extracted for review; omitted according to the model's XML `trainingValidation/omit` list. |
| `baseline_creatinine` | Baseline creatinine | Creatinine vóór operatie | `epic.LAB.Kreatinine (umol/L)` | Latest valid pre-operative value; also used as the provisional AKI-screen baseline. |
| `baseline_hemoglobin` | Baseline haemoglobin | Hemoglobine vóór operatie | `epic.LAB.Hb (mmol/L)`; `epic.LAB.poc Hb -ABL (mmol/L)` | Latest valid pre-operative value across the mapped alternatives. |

## BMI derivation

If recorded BMI is missing or nonnumeric, the processor uses `BMI = weight_kg / height_m²` when positive pre-operative weight and height are available. A valid recorded BMI is preserved. Units are configured in `config/pipeline.xml`; defaults assume weight in kilograms and height in centimeters. Supported weight units are kg/g/lb and supported height units are m/cm/in. The current parameter map does not establish actual cohort units, so verify them with the data owner before using derived BMI.

## How values are selected

The pipeline uses the configured surgery-start event as the cutoff. For time-varying parameters it takes the last valid reading strictly before that cutoff. If no cutoff can be found, a static demographic or operation/diagnosis proxy value is retained only when exactly one distinct non-missing value occurs; time-varying laboratory features remain missing.

Numeric features retain the numeric component before the first `|` and discard trailing annotations; nonnumeric text in a numeric field is missing. Categorical fields remain categorical. Missing BMI is derived from weight and height when possible; other values are not imputed. Analysis does not scale vector values. The perceptron fits numeric standardization and categorical one-hot encoding using training rows only. Heatmap-only transformations do not alter `patient_vectors.csv`. During-operation and postoperative measurements are not input features.

## Related outputs

- `patient_vectors.csv`: patient ID and the nine mapped pre-operative dimensions; no target.
- `patient_outcomes.csv`: postoperative-derived target and evidence metadata keyed by patient ID.
- `training_set.csv` and `validation_set.csv`: produced by the perceptron command; validation has no target column.
- `perceptron_model.json`: model parameters and aggregate validation metrics, without patient rows or IDs.
- `preoperative_heatmap.png`: visual overview; it does not modify the CSV values.

Pre-operative definitions are maintained directly in `<relevantParameters><pre>` in `config/pipeline.xml`; shared derivation settings live in `<relevantParameters><settings>`.
