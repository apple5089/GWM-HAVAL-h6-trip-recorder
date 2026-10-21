# Third-party notices

## Original recorder code

The recorder, database logic, local renderer, web application and notification code in this repository are distributed under the root MIT license.

## Leaflet

`static/leaflet/` contains Leaflet, licensed under the BSD 2-Clause License. Its original notice is retained in `static/leaflet/LICENSE`.

## Fonts

`static/fonts/` contains renamed Noto Sans SC subsets under the SIL Open Font License 1.1. The license is retained in `static/fonts/OFL.txt`. `SOURCE.json`, `MAP_SOURCE.json` and `DISPLAY_SOURCE.json` record the immutable upstream commit, original font digest, subset family names and weights. These fonts contain no vehicle or account information.

## Separately supplied vehicle client

The integration adapter targets interfaces observed in `moryoav/ha-gwm-ev` commit `82883e801b59b025a6a6c21a2b3179643db78040`. That source labels its client `MIT AND LicenseRef-GWM-Protocol-Materials` and records unresolved redistribution conditions in its third-party notice. No `gwm_client` package, app-derived signing/encryption constants, bootstrap identity material, certificates, APKs or decompiled source are included here. The external package must be supplied separately under an appropriate basis; see `docs/VEHICLE_CLIENT.md`.

## Services and displayed data

- Tencent map and geocoding content remains subject to Tencent's applicable terms and attribution requirements. The renderer preserves the source map's bottom attribution strip.
- Browser map data: © OpenStreetMap contributors, under ODbL, with attribution displayed by the map.
- Weather data: Open-Meteo, with CC BY 4.0 attribution displayed in generated cards when weather is present. Service access and data licensing are separate considerations.
- Feishu and Kimi are configured by each deployment using its own credentials.
- The checked-in demo is generated locally from synthetic data without service calls or real road imagery.

Runtime dependency licenses are supplied by their respective Python distributions. Project names and marks identify integrations; they are not covered by this repository's MIT grant.
