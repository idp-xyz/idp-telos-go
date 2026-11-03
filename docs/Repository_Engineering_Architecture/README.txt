IDP TELOS · Repository & Engineering Architecture Specification v1.0
Date: 2026-11-02
Status: Architecture proposal / engineering review draft (not an implementation verification).

Contents:
- IDP_TELOS_Repository_Engineering_Architecture_v1.0.html: complete offline interactive specification
- IDP_TELOS_Repository_Topology.svg: independently renderable architecture diagram
- preview_desktop_top.png / preview_390.png: browser render previews

Engineering policy:
- 7 repositories: idp-telos, idp-workspace, idp-host, idp-eer, idp-cmi, idp-mcp-infrastructure, idp-contracts.
- 5 autonomous runtime components; platform integration repo is NOT a source-code monorepo.
- EER keeps execution authority; Domain Systems keep business transaction authority.
- Data examples and version numbers are illustrative, not current releases.

Validation performed in a browser with 1440 / 768 / 390 px viewports:
- no JavaScript page errors
- no document-level horizontal overflow
- repo explorer switches, contract tabs switch, checklist updates, mobile nav operates
- SVG download tested; stand-alone SVG parsed and rendered.

This document was generated as a proposed specification. No production build or integration tests of IDP subprojects were executed.
