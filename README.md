# Pattern Recognition

Course project repository for Pattern Recognition.

## Project documents

- [Baseline system improvements](docs/baseline-system-improvements.md)
- [Patient-vector workflow and artifacts](docs/patient-vectors.md)
- [Current CSV extraction workflow](docs/data-extraction.md)
- [Pre-operative parameter guide](docs/preoperative-parameters.md)
- [Post-operative parameters and outcome definitions](docs/postoperative-outcomes.md)
- [Perceptron training and validation](docs/perceptron-training.md)
- [XML parameter map, schema, template, and XML Notepad setup](docs/parameter-map-xml.md)
- [XML pipeline run configuration](docs/xml-pipeline-configuration.md)

## Getting started

Requires Python 3.10 or newer. The logger uses only the standard library; the CSV-analysis CLI requires Matplotlib for pre-operative and post-operative heatmaps.

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m unittest discover -s tests -v
py -3 -m src.cli --help
py -3 -m src.cli run --help
```

## Editing XML with XML Notepad (Windows)

[Install XML Notepad from the official instructions](https://microsoft.github.io/XmlNotepad/install/). XML Notepad is an optional editor, not a Python dependency. Install .NET Framework 4.8 first; the official page explains the convenient ClickOnce installer and offline setup.

XML Notepad shows XML as an expandable element/attribute tree and lets you edit the selected node's value. With the XML's XSD associated, it provides schema-aware suggestions and reports validation errors as you edit. For this project, open `config/pipeline.xml` and keep `config/pipeline.xsd` alongside it; the XML references that schema.

Before editing, make a backup. Change the intended values or parameter definitions while preserving element names, IDs, nesting, and required order; consult the [XML pipeline guide](docs/xml-pipeline-configuration.md) and [parameter-map guide](docs/parameter-map-xml.md) for project rules. Don't edit the XSD just to hide a validation error. Save, then run the tests and schema checks documented below. XML Notepad checks XML structure, not project-specific meaning or runtime behavior.

The starter template points to the empty `data/patients/` directory; the active `config/pipeline.xml` may point to an external approved cohort. Review its input paths and switches before running it. A patient can have a base file and numbered parts such as `ref_0002.csv` and `ref_0002_1.csv`. To use the active configuration after approval:

```powershell
py -3 -m src.cli run
```

See the [XML pipeline configuration guide](docs/xml-pipeline-configuration.md) for stage toggles, parameter selection, train/validation mode, novel-data prediction, plotting, and paths. Direct `analyze`, `distributions`, and `train-perceptron` commands remain available.

Analyze explicitly selected patient CSVs or a directory of patient families. Numbered sibling files are grouped into one patient-level vector and outcome:

```powershell
py -3 -m src.cli analyze "C:\path\to\csvs" --output "C:\path\to\results"
py -3 -m src.cli train-perceptron --input "C:\path\to\results\patient_vectors.csv" --outcomes "C:\path\to\results\patient_outcomes.csv" --training-set "C:\path\to\results\training_set.csv" --validation-set "C:\path\to\results\validation_set.csv" --model "C:\path\to\results\perceptron_model.json" --train-fraction 0.8 --epochs 100 --seed 42
py -3 -m src.cli distributions "C:\path\to\csvs" --output "C:\path\to\results" --phase all --bins 20
```

Analysis writes three CSVs: pre-operative-only `patient_vectors.csv`, patient-keyed postoperative labels in `patient_outcomes.csv`, and `postoperative_measurements.csv` for review. The classifier emits `-1` for failure and `+1` for success; `NA` is only an unclassified/missing-evidence marker, not a third class. Training joins by `patient_id`, creates a labeled `training_set.csv` and a `validation_set.csv` containing predictions but no true target, then reports aggregate mislabeling and confusion counts in the CLI and model JSON. `--train-fraction 0.8` means 80% training and 20% validation after unclassified/incomplete rows are excluded. The configured `trainingValidation/omit/id` list controls excluded inputs; the default XML lists `operation_diagnosis_proxy`. `--omit id1,id2` adds more exclusions. See the [perceptron training guide](docs/perceptron-training.md). The classifier is not a calibrated probability estimator.

The default `results/` and `logs/` paths are ignored by Git, but custom output folders are not automatically ignored. Keep cohort inputs, outputs, and logs in approved storage and do not commit them.

### Processing modules and parameter definitions

- `config/pipeline.xml` is the canonical XML configuration. Its `<preprocessing><relevantParameters>` has shared `<settings>` and parameter definitions written **once** in `<pre>`, `<during>`, or `<post>` (currently 9 pre-operative, 0 during-operative, 11 post-operative). An `<class name="epic.CLASS">…parameters…</class>` groups defined parameters of that class; extra raw CSV columns are never silently added. Post-operative `measurement` and `outcome_criteria` entries live inside their owning parameter; class-wide `failure="nonempty"` is optional after clinical review (off by default). `epic.DEMOGRAPHICS.days mortality` is failure evidence when it contains a nonnegative number, including `0`, but its follow-up horizon is unknown. Other outcome rules remain provisional or pending; no reward scores, weights, or severity bands are used. During-operative fields can be defined for header reporting, but extraction rejects them until an operation-end rule is validated.
- `config/pipeline.xsd` validates both run settings and the embedded typed parameter catalog; `config/pipeline.template.xml` is the disabled starter. `model/activeModel` selects the model configuration. The perceptron is implemented today; model sections are designed to be extended. See the [XML configuration guide](docs/xml-pipeline-configuration.md) for the single schema, schema editing, stage order, and extension steps, and the [parameter-map editor guide](docs/parameter-map-xml.md) for catalog editing and XML Notepad.
- `src/preoperative.py` extracts pre-operative features and derives missing BMI. The same embedded catalog configures units; defaults assume kg/cm, which must be checked against the source data dictionary.
- `src/postoperative.py` contains `PostoperativeProcessor`, which exposes post-op parameters, aggregates configured measurements, and assigns the outcome target.
- `src/patient_pipeline.py` coordinates both processors and writes separate patient-level outputs and analysis artifacts.
- `src/csv_header_descriptions.py` reads only each CSV's header, inventories exact columns per file, groups numbered sibling files by patient family, and appends pre/during/post header tables, a class table per phase, Unassigned headers, and a final index of every discovered Epic class. It derives phase assignments from selected catalog fields, not from names or patient values; enable it independently under `preprocessing/csvHeaderDescriptions` (see the [XML guide](docs/xml-pipeline-configuration.md#csv-header-descriptions)).
- `src/model_config.py` defines the model-config base, registered `PerceptronModelConfig`, and reusable typed child nodes for training/validation, novel-data prediction, and plotting.
- `src/perceptron.py` contains the small online Rosenblatt trainer and validation split; omitted input IDs are supplied by the XML model configuration, while `--omit` adds further exclusions.

### How the heatmaps are made

- **Pre-operative feature heatmap:** displays all configured feature columns for a deterministic sample of up to 500 patients; it is not a cleanest-subset view.
- **Numeric columns:** each parameter is min–max scaled independently across the patients displayed: `(x - min) / (max - min)`. The minimum maps to `0`, maximum to `1`; a constant column maps to `0.5`. Missing/unparseable cells are gray.
- **Categorical columns:** one-hot encoded for the plot, with missing values represented as `NA`.
- **Pre-op missingness:** a separate binary map (`1` missing, `0` present); it is not min–max scaled.
- **Post-operative:** uses the same per-parameter numeric scaling. Unresolved or unobserved parameters remain visible as gray/empty columns.

Heatmaps use a deterministic sample of up to 500 patients; values are scaled within that displayed sample. These transformations are visual only—the CSV data and model vectors are unchanged. Values on different heatmap columns therefore should not be compared as though they shared clinical units or a common scale.

Import the logger from `src/logger.py` in project code. Each run writes a timestamped log to `logs/<UTC-run-id>/run.log` and mirrors accepted messages to stderr.

## AI assistance

Code autocompletion is used in this project. AI may be used to help write test functions and to assist with documentation writing. Authors remain responsible for reviewing and validating all submitted work.

## License

Original project code and documentation are licensed under the MIT License; see [LICENSE](LICENSE). Course materials, datasets, and third-party content remain subject to their own terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
