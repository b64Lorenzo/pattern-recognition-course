# Pattern Recognition

Course project repository for Pattern Recognition.

## Project documents

- [Project proposal](docs/project-proposal.md)
- [Baseline system improvements](docs/baseline-system-improvements.md)
- [Project finalization](docs/project-finalization.md)

## Getting started

Requires Python 3.10 or newer. The starter logger uses only the Python standard library.

```bash
python -m unittest discover -s tests -v
```

## XML Notepad (Windows)

[Install XML Notepad using the official instructions](https://microsoft.github.io/XmlNotepad/install/). It is an optional Windows editor, not a project dependency; the installer page lists the .NET Framework 4.8 prerequisite and ClickOnce or offline installation options.

XML Notepad displays an XML document as an expandable tree of elements and attributes, with a value pane for editing the selected node. When the XML is associated with an XSD schema, it can suggest schema-valid names and values and show validation errors while you edit.

For safe edits, make a backup first, open the intended `.xml` file, and change the relevant existing elements or attributes without disturbing their names, IDs, nesting, or required order. Use the project guide to decide what values belong there; do not change the `.xsd` just to hide a validation error. Save the XML, then run the project's tests and schema-validation checks. XML Notepad can catch structural problems, but it cannot verify project-specific meaning or runtime behavior.

Import the logger from `src/logger.py` in project code. Each run writes a timestamped log to `logs/<UTC-run-id>/run.log` and mirrors accepted messages to stderr.

## AI assistance

Code autocompletion is used in this project. AI may be used to help write test functions and to assist with documentation writing. Authors remain responsible for reviewing and validating all submitted work.

## License

Original project code and documentation are licensed under the MIT License; see [LICENSE](LICENSE). Course materials, datasets, and third-party content remain subject to their own terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
