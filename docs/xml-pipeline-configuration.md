# XML pipeline configuration

The project runs from `config/pipeline.xml`, validated against the **single** schema, `config/pipeline.xsd`. That schema describes the whole run configuration and the typed parameter catalog (both embedded `<relevantParameters>` and standalone `<parameterMap>` roots); there is no separate parameter-map XSD. The top-level groups map to the configuration sections, with preprocessing and model settings nested beneath them:

```xml
<pipelineConfiguration>
  <logging>...</logging>
  <preprocessing>
    <parameterSelection>...</parameterSelection>
    <parameterDescriptions>...</parameterDescriptions>
    <csvHeaderDescriptions>...</csvHeaderDescriptions>
    <plotting>...</plotting>
  </preprocessing>
  <model>
    <activeModel>perceptron</activeModel>
    <perceptron>
      <trainingValidation>...</trainingValidation>
      <novelData>...</novelData>
      <plotting>...</plotting>
    </perceptron>
  </model>
</pipelineConfiguration>
```

The real file also contains the values, explicit `run` switches, and schema reference. `config/pipeline.template.xml` is a schema-valid starter with execution switches disabled.

## Order and execution

The top-level order is intentional and validated by an XSD `xs:sequence`: `logging`, then `preprocessing`, then `model`. `<logging>` is a direct child of `<pipelineConfiguration>` and supplies shared settings; it is initialized for the run, not treated as a processing stage. The runner executes enabled processing stages in their XML order, so preprocessing outputs are ready before the model consumes them. Within each section, child elements also have a schema-defined order for consistent reading and editing.

When adding or moving a pipeline stage, change the top-level sequence in `config/pipeline.xsd`, place the matching node in that position in both XML files, and update the configuration parser and runner so the stage is executed at that point. Adding an XSD node alone only makes the XML valid; it does not implement or execute a component.

Run the configured workflow from the project root:

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m src.cli run
```

Or choose another XML file:

```powershell
py -3 -m src.cli run --config config/pipeline.xml
```

The starter template points to an empty `data/patients/` directory. The active `config/pipeline.xml` may point to an approved external cohort; **review its inputs and stage switches before running it**. A patient may have multiple numbered files in the same directory. Patient files, logs, and generated results are not source code and should not be committed.

## Configuration sections

### `logging` (direct child of `pipelineConfiguration`)

- `directory` sets the log output folder.
- `level` accepts `DEBUG`, `INFO`, `WARNING`, or `ERROR`.

### `preprocessing`

- `run` enables or skips CSV preprocessing.
- `inputs/path` lists one or more files or folders to scan.
- `outputDirectory` sets where patient vectors, outcomes, measurements, and reports are written.
- `relevantParameters` has `<settings>` for shared typed options followed by `<pre>`, `<during>`, and `<post>`. Each phase **owns its full parameter definitions**: `<parameter id="...">` contains metadata and exact source fields; `<class name="epic.CLASS">` groups matching defined parameters, without promoting unknown CSV columns. No sibling `<object>` contains a second catalog, and IDs/phases are not duplicated as typed entries. A source such as `epic.LAB.Kreatinine (umol/L)` may have distinct pre- and post-operative definitions, with no outcome leaking into predictors. `recorded_death_days` maps to `epic.DEMOGRAPHICS.days mortality` without assuming a 30-day endpoint.
- Each post-operative parameter owns its typed `outcome_criteria` and `measurement` entries. An optional `<post><class name="epic.CLASS" failure="nonempty">…defined parameters…</class></post>` treats **any nonblank, non-missing value** in selected, catalogued post-operative fields of that class as failure (`-1`), including `0` or `false`. Enable this broad rule only after clinical review; it is off in the checked-in XML. Pre-/during-operative classes cannot declare failure.
- Post-operative `measurement` entries configure aggregation and optional `window_hours`: the measurement inherits its parameter's source fields when absent, but an explicit empty source-field array means no source. `record_scope=patient` reads outcome metadata from any row; the default uses postoperative timestamps. Patient scope cannot specify `window_hours`. `measurementOrder` preserves the existing export-column order; new measurements without it follow in XML order. Patient-scope metadata is excluded from model inputs.
- `parameterSelection/run` enables a **second**, optional filter on the phase-owned definitions. When enabled, `phases` selects pre/during/post-operative phases; `includeIds` optionally narrows that set; `excludeIds` removes IDs. Empty `includeIds` means all defined parameters in those phases. The same filter is applied to preprocessing outputs, parameter documentation, and distributions; the CSV header report always uses the primary `<pre>/<during>/<post>` definitions, not this optional execution filter.
- `parameterDescriptions/run` writes a Markdown parameter reference without patient data.
- `csvHeaderDescriptions/run` independently scans the configured `inputs/path` CSVs and writes an exact, per-file header inventory plus a Markdown summary with filename families, ordered schemas, header frequency, phase-specific header/class tables, unassigned headers, and a final index of **all discovered Epic classes**. It works even when `preprocessing/run` is `false`.
- `plotting/run` controls preprocessing heatmaps; `distributions` additionally enables per-parameter plots. `phase` is currently `all`, `preoperative`, or `postoperative`, and `bins` controls histogram resolution; during-operative extraction/plots are unavailable without an operation-end rule.

The checked-in XML defines 9 pre-operative, 0 during-operative, and 11 post-operative parameters. `<during>` supports explicit definitions and header reporting, but **patient-level extraction refuses to run with a defined during-operative parameter** until a validated operation-end rule exists. It does not label every row after surgery starts as “during.” The default XML has no class-wide failure flags; only individually reviewed provisional postoperative rules are active.

#### Phase-owned parameter example

```xml
<relevantParameters formatVersion="1">
  <settings><!-- Shared typed entries (see the checked-in XML). --></settings>
  <pre>
    <class name="epic.DEMOGRAPHICS">
      <parameter id="age">
        <entry name="source_fields"><array><item><string>epic.DEMOGRAPHICS.age</string></item></array></entry>
        <!-- Other required workbook metadata. -->
      </parameter>
    </class>
  </pre>
  <during/>
  <post>
    <parameter id="recorded_death_days" measurementOrder="8">
      <entry name="source_fields"><array><item><string>epic.DEMOGRAPHICS.days mortality</string></item></array></entry>
      <!-- Other required workbook metadata. -->
      <entry name="measurement"><object>
        <entry name="aggregation"><string>max</string></entry>
        <entry name="record_scope"><string>patient</string></entry>
      </object></entry>
      <entry name="outcome_criteria"><object>
        <entry name="success"><null/></entry>
        <entry name="failure"><object>
          <entry name="operator"><string>gte</string></entry>
          <entry name="value"><number>0</number></entry>
        </object></entry>
      </object></entry>
    </parameter>
  </post>
</relevantParameters>
```

This abbreviated excerpt omits required metadata and other shared settings; use the checked-in XML for a complete runnable file. To add a parameter, put its **definition once** in the appropriate phase, directly or inside a matching Epic class group. An empty `<parameter id="…"/>` does not select a definition elsewhere. A class uses only its nested, explicitly defined parameters; other headers in that class remain unassigned until defined. The individual outcome and measurement belong inside the post-operative parameter. Old XML using a sibling `<object>` catalog and empty selectors must be migrated. Standalone `<parameterMap>` files keep their one-object representation.

### CSV header descriptions

The starter template leaves this component **off by default**. To inspect headers without extracting patient rows or training a model, copy `config/pipeline.template.xml` to another file **in `config/`**, point `<preprocessing><inputs><path>` at the approved CSV folder/files, and set only `<preprocessing><csvHeaderDescriptions><run>` to `true`. Leave `<preprocessing><run>`, `<model><perceptron><run>`, and all other component switches `false`:

```xml
<csvHeaderDescriptions>
  <run>true</run>
  <inventoryOutput>results/csv_headers.csv</inventoryOutput>
  <summaryOutput>results/csv_header_summary.md</summaryOutput>
</csvHeaderDescriptions>
```

```powershell
py -3 -m src.cli run --config config/pipeline.headers.xml
```

`csv_headers.csv` records **source input number**, CSV filename relative to that input, family, numbered part, 1-based column position, and the **exact** header text for every CSV. The Markdown summary lists all files and counts, ordered header-schema groups, patient filename families, and each distinct header's **file** frequency (not patient frequency). At the end it adds `Pre-operative headers`, `During-operative headers`, and `Post-operative headers`, each with an exact-header table and a **class table**; a header can appear in two phase tables when separate catalog records assign it to both. The `Unassigned headers` table retains everything not selected by an exact catalog field, and the final `Epic class index` enumerates **all discovered** `epic.CLASS.item` classes, including classes with only unassigned headers. Phase assignment is from the XML, **not inferred from a class name or from patient rows**. Non-Epic fields may appear in a phase header table but have no Epic class.

The family convention matches preprocessing: `ref_0002.csv` and `ref_0002_1.csv` are one patient, while `ref_0003.csv` is another; a zero-padded final identifier of three or more digits remains part of the base name. `case_1.csv` and `case_2.csv` share the `case` family even without `case.csv`. Families never span directories, but can include separately configured input files in the same directory. Source input numbers remain in the per-file inventory. Identical headers in different files are still recorded per file; duplicate columns in a single file retain both positions. Quoted commas and tab-delimited headers are supported. Source directories are scanned recursively; the configured `outputDirectory` is excluded from input discovery.

Only the first CSV record is decoded for each file, **not** patient rows or measurements. File names themselves may be patient identifiers: treat both reports as sensitive, store them only in approved local storage, and never commit or post them. Logs include aggregate counts, not filenames. The inventory preserves header text exactly; open it in a text/CSV-aware viewer rather than a spreadsheet that could interpret untrusted header strings as formulas. This inventory does **not** modify the workbook or the embedded `relevantParameters` catalog; it provides evidence to review their field coverage separately.

### `model`

`activeModel` selects a model name registered to a class in `src/model_config.py`. Each model class parses its own child nodes and can compose reusable nodes such as `TrainingValidationConfig`, `NovelDataConfig`, and `PlotConfig`. **Perceptron is the only model implemented now.** To add another runnable model, add and register its class, add its name and XML type/section to the XSD, update both XML files, add its runner in `pipeline_runner.py`, and test its configuration and execution behavior.

### `model/perceptron`

- `run` enables or skips perceptron execution when it is the active model.
- `mode` is either `train-validation` or `novel-data`.
- `trainingValidation` sets input vectors/outcomes when preprocessing is skipped, the labeled training CSV, the prediction-only validation CSV, model output, train fraction, epochs, seed, and the XML `omit/id` list. The default config omits `operation_diagnosis_proxy`; change that list to change exclusions. The direct CLI `--omit` option adds further exclusions. If preprocessing runs, training consumes its vectors and outcomes automatically.
- `novelData` selects feature-only vectors, a previously saved model, and the output predictions CSV. If preprocessing runs, the freshly generated vectors are used. Novel prediction CSVs contain `prediction`, never a postoperative `target`.
- `plotting/run` optionally writes an aggregate validation confusion-matrix image; it is only valid in `train-validation` mode.

### `model/perceptron/mode` examples

For training and holdout validation:

```xml
<mode>train-validation</mode>
```

For applying a saved model to novel feature vectors:

```xml
<mode>novel-data</mode>
```

Set the corresponding paths under `trainingValidation` or `novelData`. The model uses only configured pre-operative features; postoperative data remains outcome evidence, not model input.

## Editing and validating

Edit the master file with an XML editor such as XML Notepad; installation details are in the [XML parameter-map guide](parameter-map-xml.md). The master XML points to `pipeline.xsd` through `xsi:noNamespaceSchemaLocation`. From the repository root, validate the master file and template with `xmllint` when available:

```text
xmllint --noout --schema config/pipeline.xsd config/pipeline.xml config/pipeline.template.xml
```

The XSD checks document structure, values, and allowed modes. The Python loader separately checks runtime requirements such as supported phase names, positive training settings, and whether model plotting is compatible with the selected mode. Schema validation alone does not guarantee the input files exist or that there are enough labeled cases to train.

Paths in the XML are relative to the project root (the parent of `config/`); keep alternate run-control XML files directly in `config/` so relative paths resolve consistently. The direct `analyze`, `distributions`, and `train-perceptron` commands remain available, but `run` is the single XML-driven workflow.

## Extending the schema and pipeline

Edit only `config/pipeline.xsd`. It owns both global XML roots (`pipelineConfiguration` and `parameterMap`) and the shared typed value and phase-definition types. Every element declaration and named type has an `xs:annotation/xs:documentation` description; add the same kind of short, user-facing explanation for every new element, attribute, or type. For example:

```xml
<xs:element name="run" type="xs:boolean">
  <xs:annotation>
    <xs:documentation>Whether this component is executed.</xs:documentation>
  </xs:annotation>
</xs:element>
```

Use `xs:sequence` for ordered children. Its order is the XML order and the intended execution order. Use `xs:complexType` for a component's child settings, and `xs:simpleType` restrictions for allowed values such as model names and execution modes.

### Add a setting or component

- **Root-level shared setting:** add the element to the `pipelineConfiguration` `xs:sequence` in `config/pipeline.xsd`, add it directly under `<pipelineConfiguration>` in both XML files, document it with `xs:documentation`, parse it in `load_pipeline_config`, and pass it to the logger or stage that consumes it. Shared settings are not a processing stage by themselves.
- **Preprocessing component:** add its element at the intended position in `PreprocessingType`, define/document its type, and add the same node to both XML files. Parse and validate its values in `pipeline_config.py`; implement the component and call it in `pipeline_runner.py` before the model stage when that is its declared position. Give executable components an explicit `run` switch and test enabled and disabled behavior using synthetic inputs.
- **Model:** define a `ModelConfig` subclass in `src/model_config.py` and register it with its XML model name. Parse the model's own children there, reusing node classes such as `TrainingValidationConfig`, `NovelDataConfig`, or `PlotConfig` where the XML shape matches. Add its name to `ModelName`, define and document its XSD type/section, and add it to both XML files. Register its execution handler in `pipeline_runner.py`; test class parsing, required/invalid children, modes, and input/output contracts. `perceptron` is the only implemented model now, so registering a parser alone does not make a model runnable.
- **New pipeline node:** add the node to the root `pipelineConfiguration` sequence at its execution position, create/document its type, update both XML files, parse it, and add its execution handler at the same position in `pipeline_runner.py`. Add a synthetic end-to-end test that proves the ordering and `run` behavior.

After changes, validate the active file and template with the command above, run `py -3 -m unittest discover -s tests -v`, and update this guide if the node's purpose or ordering changes. The same XSD can validate a standalone catalog explicitly with `xmllint --noout --schema config/pipeline.xsd path/to/catalog.xml`; standalone maps need not carry a second schema reference.
