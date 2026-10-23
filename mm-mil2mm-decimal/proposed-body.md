`mil2mm("10.0")` currently returns 10 millimeters, while `mil2mm(10)` and `mil2mm("10")` return 0.254 millimeters. Signed and fractional unitless strings fall through to the general `mm` parser because the mil-default branch recognizes only digits.

Recognize ordinary signed decimal strings, with surrounding whitespace, in the existing mil-default branch. Explicit unit strings still use their supplied units. The README now shows the conversion rule. Scientific notation, hexadecimal and non-finite input behavior are unchanged.

Validation at `fa997a5cdfabd854c4007e80a14a2869a7d0152c`:
- Bun 1.4.4: all 28 tests pass. Before the change, 10 new decimal/sign/whitespace regressions fail and 11 controls pass.
- TypeScript type check, Biome format check and tsup build pass.
- Independent checks pass for 108 decimal inputs and 53 baseline compatibility cases, including all eight explicit units.
- Dependencies installed with `bun install --frozen-lockfile`; both lockfiles are unchanged.

The exported conversion error is reproduced directly; no downstream production incident is claimed. AI assistance was used for implementation and review. Hosted CI and upstream review have not run.

