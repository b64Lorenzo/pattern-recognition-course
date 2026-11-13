# XML configuration and XML Notepad

The **single source of truth** for execution settings and parameter definitions is `config/pipeline.xml`. Within `<preprocessing><relevantParameters>`, `<settings>` contains shared typed options, and each parameter is defined **once** inside its `<pre>`, `<during>`, or `<post>` phase (optionally grouped under `<class>`). There is no sibling parameter-catalog `<object>` and no second list of selected IDs. The active map has 9 pre-operative, 0 during-operative, and 11 post-operative definitions. The separate workbook is not bundled or read by the program. `recorded_death_days` is a user-added exact-header field absent from that workbook (`workbook_row` is null); other definitions retain workbook provenance. During-operative definitions work for header reporting, but extraction rejects them until an end-of-operation rule is validated.

`config/pipeline.xsd` is the one schema file. It validates both the full pipeline document and the typed parameter catalog—embedded in `<relevantParameters>` or used as a standalone `<parameterMap>`. There is no separate parameter-map schema. `config/pipeline.template.xml` is a complete starter with all execution switches turned off and includes the parameter definitions.

## Catalog structure

The XML uses typed entries: each `<entry name="…">` contains exactly one `<string>`, `<number>`, `<boolean>`, `<null/>`, `<object>`, or `<array>` value; arrays contain ordered `<item>` values. Shared entries go under `<settings>`. Definitions go in their phase; the parameter's `id` attribute and enclosing phase replace redundant typed `id`/`phase` entries. A class groups only its **nested, explicitly defined** parameters with matching `epic.CLASS.item` source fields; it does not import every CSV column in that class. For example (other required workbook metadata omitted for clarity):

```xml
<relevantParameters formatVersion="1">
  <settings>
    <entry name="missing_value_tokens"><array><item><string>NA</string></item></array></entry>
    <!-- Other shared configuration entries. -->
  </settings>
  <pre>
    <class name="epic.DEMOGRAPHICS">
      <parameter id="age">
        <entry name="source_fields"><array><item><string>epic.DEMOGRAPHICS.age</string></item></array></entry>
        <!-- Required workbook metadata. -->
      </parameter>
    </class>
  </pre>
  <during/>
  <post>
    <parameter id="recorded_death_days" measurementOrder="8">
      <entry name="source_fields"><array><item><string>epic.DEMOGRAPHICS.days mortality</string></item></array></entry>
      <!-- Required workbook metadata. -->
      <entry name="measurement">
        <object><entry name="aggregation"><string>max</string></entry></object>
      </entry>
      <entry name="outcome_criteria">
        <object>
          <entry name="success"><null/></entry>
          <entry name="failure"><object>
            <entry name="operator"><string>gte</string></entry>
            <entry name="value"><number>0</number></entry>
          </object></entry>
        </object>
      </entry>
    </parameter>
  </post>
</relevantParameters>
```

`measurement` and `outcome_criteria` are typed entries **inside the owning post-operative parameter**, not separate lists. The measurement inherits the parameter's `source_fields` when omitted; use an explicit empty array to mean no measurement source. `measurementOrder` preserves the existing `postoperative_measurements.csv` column order; new measurements without it follow explicitly ordered ones in XML order. Only `<post><class name="epic.CLASS" failure="nonempty">…</class></post>` can opt into class-wide failure, which requires clinical review and is off by default. The same exact source header may have distinct pre- and post-operative parameter definitions when both roles are intended; no outcome is ever a pre-operative model input.

When editing, keep `<settings>`, `<pre>`, `<during>`, and `<post>` in order. Parameter IDs must be unique across phases; a class must contain at least one defined parameter with a matching source field. Do not duplicate `id`/`phase` as typed entries or define a second parameters/measurements array under settings. Missing outcome criteria stay unclassified; do not invent cutoffs. Old XML files using `<object>` plus empty `<parameter id="…"/>` selectors and separate `<outcome id="…">` rules need migration. Standalone `<parameterMap>` files retain their one-object structure for the separate direct-map CLI format.

The XSD checks document structure and typed values. The Python loader additionally enforces runtime requirements such as phase scope, required metadata, and unique parameter IDs. A schema-valid file can still fail those application checks.

## Install XML Notepad on Windows

XML Notepad is an optional editor, not a Python or CLI dependency.

1. Ensure .NET Framework 4.8 is installed, as listed in XML Notepad's official setup instructions.
2. Open the [official XML Notepad install page](https://microsoft.github.io/XmlNotepad/install/) and choose the ClickOnce installer for the simplest setup. For offline installation, download the standalone ZIP there, extract it, and run `XmlNotepadSetup.msi`. The [official GitHub releases](https://github.com/microsoft/XmlNotepad/releases/latest) also provide a Windows MSIX installer under **Assets**.
3. Open `config/pipeline.xml` in XML Notepad. Keep `config/pipeline.xsd` beside it; the XML points to the schema with `xsi:noNamespaceSchemaLocation="pipeline.xsd"`. XML Notepad can validate while editing and show errors in its Error List.
4. Save the file and run the project tests:

```powershell
py -3 -m unittest discover -s tests -v
```

To validate both the active configuration and starter template from the project root when `xmllint` is available:

```text
xmllint --noout --schema config/pipeline.xsd config/pipeline.xml config/pipeline.template.xml
```

The project does not install XML Notepad automatically. Use the official project site or its official GitHub releases for downloads. The project XML has no DTD; do not enable DTD processing for untrusted XML files.
