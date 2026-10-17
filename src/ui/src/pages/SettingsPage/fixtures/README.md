# Manual package fixture

`manual-panel.zip` is a deterministic 801-byte static package for the browser file
picker and multipart transport checks. It contains `manifest.json`, a minimal
`panel/index.html` and the host-origin placeholder module. It has no credentials,
server runtime or SDK handshake. It is not a live external app acceptance test.

The existing server `validate_package` accepts its three files and declaration
key `example-panel`. SHA-256:
`752d20fa41e8677a040aa74a820ef076ab770f3bde42a7d77711e6aceaaed565`.
Inspect with `python -m zipfile -l manual-panel.zip`.
