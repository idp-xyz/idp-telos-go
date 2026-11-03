# Workspace / Web / Desktop integration

1. Copy `01_Master/TELOS_Primary_Locked_Transparent.svg` (static) and the relevant `02_BinaryE` SVG into your own public `/brand/` assets directory.
2. Do not reconstruct TELOS via CSS, custom fonts, reflowed characters, or `object-fit:fill`; preserve aspect ratio.
3. For website header/nav, prefer static transparent SVG. For startup / hero, optionally use animated Binary E. Provide reduced-motion fallback.
4. For favicon and tiny UI, use the provided *secondary utility* three-bar E icon; this is not a replacement for the complete wordmark in official brand presentation.
5. Load logos locally, use immutable filenames or asset versioning, and do not make runtime authentication dependent on SVG animation.
6. The visual wordmark TELOS is separate from the intended full trademark text IDP TELOS; obtain appropriate trademark review prior to public commercial rollout.

## Example (HTML)
```html
<img src="/brand/TELOS_Primary_Locked_Transparent.svg" alt="TELOS" height="48" width="170" style="width:auto;max-width:100%;height:48px" />
```

## Important
The source master has a 1536x512 artboard; its glyphs are centered in that artboard. For dense navigation, use the cropped, transparent master while keeping all path data unchanged.
