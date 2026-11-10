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

Import the logger from `src/logger.py` in project code. Each run writes a timestamped log to `logs/<UTC-run-id>/run.log` and mirrors accepted messages to stderr.

## AI assistance

Code autocompletion is used in this project. AI may be used to help write test functions and to assist with documentation writing. Authors remain responsible for reviewing and validating all submitted work.

## License

Original project code and documentation are licensed under the MIT License; see [LICENSE](LICENSE). Course materials, datasets, and third-party content remain subject to their own terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
