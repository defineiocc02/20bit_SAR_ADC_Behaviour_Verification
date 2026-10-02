# Physical-flow acceptance checks

The six listed pytest groups pass 272 tests, including 109 buffered-clock flow and report-parser cases. They exercise real Vivado 2018.3 report fixtures and injected failures. They do not run Vivado or establish actual circuit timing. Source hashes, command and the preserved log are in `manifest.json`.

The earlier invocation named a nonexistent test file and executed no tests; its diagnostic log is separately identified in the manifest. The successful invocation uses the exact six files registered by CI.
