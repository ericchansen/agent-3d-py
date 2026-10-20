# Application contract snapshot

Source: [ericchansen/agent-3d-ui contracts](https://github.com/ericchansen/agent-3d-ui/tree/main/contracts), MIT licensed. This repository vendors the original application schema and synthetic fixtures for offline conformance; it does not vendor the AG-UI protocol.

Schema SHA256 (LF-normalized): `5CF50D89D10BD2EE1E5EA05AE0BAB552FB5F8C569548641BE1E96ACEB3185D38`.

The schema and fixtures are reviewed together with [Pydantic semantic validation](../models.py). Object ids and selections must be unique; selection/highlight ids must exist. Spheres require equal extents, and cylinders require equal x/z extents. All numeric values must be finite.

An edit has at least one non-null field. Null or absent color/position leaves that property unchanged. Absolute positions apply to every selected target. Approval preserves the exact native tool arguments; previews are not committed state.
